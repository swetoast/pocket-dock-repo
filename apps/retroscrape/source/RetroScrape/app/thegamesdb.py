import binascii
import hashlib
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from PIL import Image, ImageOps

from .storage import durable_json, mark_persistent_write, write_bytes_if_changed
from . import USER_AGENT
from .database_index import iter_processable_files
from .xmame import Cancelled

API_ROOT = "https://api.thegamesdb.net"
IGNORED = {".png", ".jpg", ".jpeg", ".webp", ".xml", ".json", ".txt", ".log", ".cfg", ".ini", ".sav", ".srm", ".state", ".mp4", ".pdf"}
ARCHIVES = {".7z", ".rar", ".tar", ".gz", ".bz2", ".xz"}
RESAMPLE_LANCZOS = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
ARTWORK_MAX_SIZE = (640, 640)
class ProviderPaused(RuntimeError):
    def __init__(self, result):
        super().__init__(result.get("message", "TheGamesDB scraping paused"))
        self.result = result

PLATFORM_NAMES = {
    "amiga": ("Commodore Amiga", "Amiga"), "gamecube": ("Nintendo GameCube", "GameCube"),
    "nes": ("Nintendo Entertainment System (NES)", "Nintendo Entertainment System"),
    "snes": ("Super Nintendo (SNES)", "Super Nintendo Entertainment System"),
    "wii": ("Nintendo Wii", "Wii"), "wiiu": ("Nintendo Wii U", "Wii U"),
    "neogeo": ("Neo Geo",), "saturn": ("Sega Saturn", "Saturn"), "mame": ("Arcade", "MAME"),
    "gb": ("Nintendo Game Boy", "Game Boy"), "gbc": ("Nintendo Game Boy Color", "Game Boy Color"),
    "gba": ("Nintendo Game Boy Advance", "Game Boy Advance"), "n64": ("Nintendo 64",),
    "nds": ("Nintendo DS",), "dreamcast": ("Sega Dreamcast", "Dreamcast"),
    "genesis": ("Sega Genesis", "Sega Mega Drive"), "mastersystem": ("Sega Master System",),
    "gamegear": ("Sega Game Gear",), "ps1": ("Sony Playstation", "Sony PlayStation"),
    "ps2": ("Sony Playstation 2", "Sony PlayStation 2"), "psp": ("Sony PSP", "Sony Playstation Portable"),
}

class TheGamesDBScraper:
    def __init__(self, app_dir, config, profile, system, rom_dir, emit, stop_event):
        self.app_dir = Path(app_dir)
        self.config = config
        self.profile = profile
        self.system = system
        self.rom_dir = Path(rom_dir)
        self.emit = emit
        self.stop_event = stop_event
        self.settings = config["thegamesdb"]
        self.cache = self.app_dir / "data" / "thegamesdb-cache"
        self.api_key_path = Path(self.settings.get("api_key_file", self.app_dir / "config" / "thegamesdb.key"))
        self.timeout = int(self.settings.get("timeout", 30))
        self.api_key = ""
        self.allowance = None
        self.extra_allowance = 0
        self.refresh_timer = None
        self.retry_after = 0
        self.state_path = self.app_dir / "data" / "thegamesdb-state.json"
        self.state = {}
        self.cache_hits = 0
        self.network_requests = 0
        self._checkpoint_changes = 0
        self._last_checkpoint = time.monotonic()

    def check_cancelled(self):
        if self.stop_event.is_set():
            if self._checkpoint_changes:
                self._checkpoint_state(force=True)
            raise Cancelled()

    @staticmethod
    def clean_title(path):
        title = Path(path).stem
        title = re.sub(r"\s*[\(\[].*?[\)\]]", "", title)
        title = re.sub(r"[_\.]+", " ", title)
        return re.sub(r"\s+", " ", title).strip()

    @staticmethod
    def _parse_retroarch_config(path):
        values = {}
        pattern = re.compile(r'^\s*([A-Za-z0-9_]+)\s*=\s*"(.*)"\s*$')
        try:
            lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            return values
        for line in lines:
            match = pattern.match(line)
            if match:
                values[match.group(1)] = match.group(2)
        return values

    @staticmethod
    def _active_retroarch():
        config = Path("/.config/retroarch/retroarch.cfg")
        if not config.is_file():
            return None, None
        values = TheGamesDBScraper._parse_retroarch_config(config)
        candidate = values.get("libretro_directory", "")
        if candidate.startswith(":/"):
            for binary in Path("/mnt/vendor").rglob("retroarch"):
                if binary.is_file() and (binary.parent / candidate[2:]).exists():
                    return config, binary
        fallback = Path("/mnt/vendor/deep/retro/retroarch")
        return (config, fallback) if fallback.is_file() else (None, None)

    @staticmethod
    def _resolve_ra_path(value, root, config_path):
        if not value:
            return None
        if value == ":":
            return root
        if value.startswith(":/"):
            return root / value[2:]
        if value.startswith("~/"):
            # The active TF1 config is rooted at /.config/retroarch.
            if str(config_path).startswith("/.config/"):
                return Path("/") / value[2:]
            return Path.home() / value[2:]
        return Path(value)

    def _retroarch_context(self):
        config_path, binary = self._active_retroarch()
        if not config_path or not binary:
            return {"available": False, "reason": "TF1 RetroArch configuration not found"}
        values = self._parse_retroarch_config(config_path)
        root = binary.resolve().parent
        playlist_root = self._resolve_ra_path(values.get("playlist_directory", ":/playlists"), root, config_path)
        thumbnail_root = self._resolve_ra_path(values.get("thumbnails_directory", ":/thumbnails"), root, config_path)
        if not playlist_root or not playlist_root.is_dir():
            return {"available": False, "reason": "RetroArch playlist directory not found", "config": str(config_path)}
        if not thumbnail_root:
            return {"available": False, "reason": "RetroArch thumbnail directory unresolved", "config": str(config_path)}
        entries = {}
        playlists = []
        for playlist in sorted(playlist_root.glob("*.lpl")):
            try:
                payload = json.loads(playlist.read_text(encoding="utf-8", errors="ignore"))
            except (OSError, ValueError):
                continue
            items = [item for item in payload.get("items", []) if isinstance(item, dict)]
            labels = {str(item.get("label", "")) for item in items if item.get("label")}
            playlists.append((playlist, labels))
            for item in items:
                value = item.get("path")
                label = item.get("label")
                if value and label:
                    try:
                        key = str(Path(value).resolve())
                    except OSError:
                        key = str(Path(value).absolute())
                    entries[key] = {"label": str(label), "playlist": playlist.name, "db_name": str(item.get("db_name", ""))}
        collections = [path for path in thumbnail_root.iterdir() if path.is_dir()] if thumbnail_root.is_dir() else []
        mapping = {}
        for playlist, labels in playlists:
            best = None
            for collection in collections:
                stems = set()
                for category in ("Named_Boxarts", "Named_Snaps", "Named_Titles", "Named_Logos"):
                    directory = collection / category
                    if directory.is_dir():
                        stems.update(item.stem for item in directory.iterdir() if item.is_file())
                score = len(labels & stems)
                if score and (best is None or score > best[0]):
                    best = (score, collection)
            if best:
                mapping[playlist.name] = best[1]
        return {"available": True, "config": str(config_path), "binary": str(binary), "playlist_root": playlist_root, "thumbnail_root": thumbnail_root, "entries": entries, "mapping": mapping, "allow_non_png": values.get("playlist_allow_non_png", "false").casefold() == "true"}

    @staticmethod
    def _retroarch_thumbnail_name(label):
        # RetroArch replaces these playlist-label characters in thumbnail filenames.
        return re.sub(r'[&*/:`<>?\\|]', "_", str(label))

    @staticmethod
    def _publish_retroarch_boxart(source, target):
        source = Path(source)
        target = Path(target).with_suffix(".png")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".png.tmp")
        with Image.open(str(source)) as image:
            image.convert("RGBA").save(str(temporary), format="PNG")
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(target))
        mark_persistent_write()
        return target

    def _read_key(self):
        try:
            key = self.api_key_path.read_text(encoding="utf-8").strip()
        except OSError:
            raise RuntimeError("TheGamesDB API key is missing: %s" % self.api_key_path)
        if not key or any(char.isspace() for char in key):
            raise RuntimeError("TheGamesDB API key file is empty or invalid")
        self.api_key = key

    def _load_state(self):
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.state = state if isinstance(state, dict) else {}
        except (OSError, ValueError):
            self.state = {}
        self.retry_after = int(self.state.get("retry_after", 0) or 0)
        self.allowance = self.state.get("remaining_monthly_allowance", self.allowance)
        self.extra_allowance = int(self.state.get("extra_allowance", self.extra_allowance) or 0)
        self.refresh_timer = self.state.get("allowance_refresh_timer", self.refresh_timer)

    def _save_state(self):
        self.state["retry_after"] = int(self.retry_after or 0)
        if self.allowance is not None:
            self.state["remaining_monthly_allowance"] = self.allowance
        self.state["extra_allowance"] = int(self.extra_allowance or 0)
        if self.refresh_timer is not None:
            self.state["allowance_refresh_timer"] = self.refresh_timer
        self.state["updated"] = int(time.time())
        usage = self.state.setdefault("usage", {})
        period = self.state.setdefault("usage_period", {"started_at": int(time.time()), "reset_at": None, "network_requests": 0, "cache_hits": 0})
        usage["network_requests"] = int(usage.get("network_requests", 0) or 0) + int(self.network_requests)
        usage["cache_hits"] = int(usage.get("cache_hits", 0) or 0) + int(self.cache_hits)
        period["network_requests"] = int(period.get("network_requests", 0) or 0) + int(self.network_requests)
        period["cache_hits"] = int(period.get("cache_hits", 0) or 0) + int(self.cache_hits)
        self.network_requests = 0
        self.cache_hits = 0
        try:
            durable_json(self.state_path, self.state)
        except OSError:
            pass

    def _checkpoint_state(self, force=False):
        self._checkpoint_changes += 1
        now = time.monotonic()
        if force or self._checkpoint_changes >= 25 or now - self._last_checkpoint >= 15:
            self._save_state()
            self._checkpoint_changes = 0
            self._last_checkpoint = now

    def _update_usage_period(self, reset_at):
        now = int(time.time())
        period = self.state.get("usage_period")
        if not isinstance(period, dict):
            period = {"started_at": now, "reset_at": reset_at, "network_requests": 0, "cache_hits": 0}
            self.state["usage_period"] = period
            return
        previous = period.get("reset_at")
        if previous is not None and reset_at is not None:
            try:
                changed = abs(int(previous) - int(reset_at)) > 300
                passed = now >= int(previous)
            except (TypeError, ValueError):
                changed, passed = True, False
            if changed and passed:
                period = {"started_at": now, "reset_at": reset_at, "network_requests": 0, "cache_hits": 0}
                self.state["usage_period"] = period
                return
        period["reset_at"] = reset_at
        period.setdefault("started_at", now)
        period.setdefault("network_requests", 0)
        period.setdefault("cache_hits", 0)

    def _usage_status(self):
        lifetime = self.state.get("usage", {})
        period = self.state.get("usage_period", {})
        return {
            "period_network_requests": int(period.get("network_requests", 0) or 0),
            "period_cache_hits": int(period.get("cache_hits", 0) or 0),
            "period_started_at": period.get("started_at"),
            "period_reset_at": period.get("reset_at"),
            "lifetime_network_requests": int(lifetime.get("network_requests", 0) or 0),
            "lifetime_cache_hits": int(lifetime.get("cache_hits", 0) or 0),
        }

    def cache_status(self):
        now = time.time()
        max_age = int(self.settings.get("cache_days", 30)) * 86400
        count = expired = size = 0
        oldest = None
        if self.cache.is_dir():
            for path in self.cache.rglob("*.json"):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                count += 1
                size += stat.st_size
                oldest = stat.st_mtime if oldest is None else min(oldest, stat.st_mtime)
                if now - stat.st_mtime > max_age:
                    expired += 1
        return {"cache_entries": count, "cache_expired": expired, "cache_bytes": size, "cache_oldest": int(oldest) if oldest else None}

    def cleanup_expired_cache(self):
        now = time.time()
        max_age = int(self.settings.get("cache_days", 30)) * 86400
        removed = 0
        if self.cache.is_dir():
            for path in self.cache.rglob("*.json"):
                try:
                    if now - path.stat().st_mtime > max_age:
                        path.unlink()
                        removed += 1
                except OSError:
                    continue
            for directory in sorted((item for item in self.cache.rglob("*") if item.is_dir()), reverse=True):
                try:
                    directory.rmdir()
                except OSError:
                    pass
        return removed

    def allowance_status(self):
        """Fetch the non-billable TheGamesDB allowance status."""
        if not self.api_key:
            self._read_key()
        query = urllib.parse.urlencode({"apikey": self.api_key})
        request = urllib.request.Request(
            API_ROOT + "/v1/API/Limit?" + query,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise RuntimeError("TheGamesDB key is invalid or access was denied")
            raise RuntimeError("TheGamesDB allowance check failed: HTTP %s" % error.code)
        except (OSError, urllib.error.URLError, TimeoutError, ValueError) as error:
            raise RuntimeError("TheGamesDB allowance check failed: %s" % error)
        remaining = payload.get("remaining_monthly_allowance")
        extra = payload.get("extra_allowance", 0)
        timer = payload.get("allowance_refresh_timer")
        if remaining is None:
            raise RuntimeError("TheGamesDB allowance response is incomplete")
        self._load_state()
        self.allowance = int(remaining)
        self.extra_allowance = int(extra or 0)
        self.refresh_timer = None if timer is None else int(timer)
        self.state["remaining_monthly_allowance"] = self.allowance
        self.state["extra_allowance"] = self.extra_allowance
        self.state["allowance_refresh_timer"] = self.refresh_timer
        self.state["allowance_checked_at"] = int(time.time())
        reset_at = None if self.refresh_timer is None else int(time.time()) + self.refresh_timer
        self._update_usage_period(reset_at)
        self._save_state()
        result = {
            "remaining_monthly_allowance": self.allowance,
            "extra_allowance": self.extra_allowance,
            "total_available_allowance": self.usable_allowance(),
            "allowance_refresh_timer": self.refresh_timer,
            "checked_at": self.state.get("allowance_checked_at"),
        }
        result.update(self._usage_status())
        result.update(self.cache_status())
        result["local_network_requests"] = result["lifetime_network_requests"]
        result["local_cache_hits"] = result["lifetime_cache_hits"]
        return result

    def cached_allowance_status(self):
        self._load_state()
        result = {
            "remaining_monthly_allowance": self.state.get("remaining_monthly_allowance"),
            "extra_allowance": int(self.state.get("extra_allowance", 0) or 0),
            "total_available_allowance": ((int(self.state.get("remaining_monthly_allowance", 0) or 0) + int(self.state.get("extra_allowance", 0) or 0)) if self.state.get("remaining_monthly_allowance") is not None else None),
            "allowance_refresh_timer": self.state.get("allowance_refresh_timer"),
            "checked_at": self.state.get("allowance_checked_at"),
        }
        result.update(self._usage_status())
        result.update(self.cache_status())
        result["local_network_requests"] = result["lifetime_network_requests"]
        result["local_cache_hits"] = result["lifetime_cache_hits"]
        return result

    def usable_allowance(self):
        if self.allowance is None:
            return None
        return max(0, int(self.allowance or 0)) + max(0, int(self.extra_allowance or 0))

    def _pause_for_allowance(self, payload=None):
        payload = payload or {}
        timer = payload.get("allowance_refresh_timer", self.refresh_timer)
        try:
            timer = max(60, int(timer))
        except (TypeError, ValueError):
            timer = 3600
        self.refresh_timer = timer
        if "remaining_monthly_allowance" in payload:
            self.allowance = int(payload.get("remaining_monthly_allowance", 0) or 0)
        if "extra_allowance" in payload:
            self.extra_allowance = int(payload.get("extra_allowance", 0) or 0)
        self.retry_after = int(time.time()) + timer
        self.state["pause_reason"] = "allowance"
        self._save_state()
        raise ProviderPaused({"provider": "TheGamesDB", "status": "Paused", "pause_reason": "allowance", "message": "Allowance reserve reached; progress saved", "retry_after": self.retry_after, "remaining_monthly_allowance": self.allowance, "extra_allowance": self.extra_allowance, "total_available_allowance": self.usable_allowance()})

    def _pause_for_service(self, message):
        delay = max(60, int(self.settings.get("max_backoff_seconds", 60)))
        self.retry_after = int(time.time()) + delay
        self.state["pause_reason"] = "service"
        self.state["last_error"] = str(message)
        self._save_state()
        raise ProviderPaused({"provider": "TheGamesDB", "status": "Paused", "pause_reason": "service", "message": "TheGamesDB is unavailable; progress saved", "retry_after": self.retry_after, "remaining_monthly_allowance": self.allowance, "error": str(message)})

    def _wait(self, seconds):
        deadline = time.monotonic() + max(0, seconds)
        while time.monotonic() < deadline:
            self.check_cancelled()
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))

    def _request(self, endpoint, params, cache_name=None):
        self.check_cancelled()
        cache_path = self.cache / cache_name if cache_name else None
        max_age = int(self.settings.get("cache_days", 30)) * 86400
        if cache_path and cache_path.is_file() and time.time() - cache_path.stat().st_mtime <= max_age:
            try:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                self.cache_hits += 1
                self._checkpoint_state()
                return payload
            except (OSError, ValueError):
                pass
        query = dict(params)
        query["apikey"] = self.api_key
        url = API_ROOT + endpoint + "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
        maximum = int(self.settings.get("max_retries", 4))
        base = int(self.settings.get("backoff_seconds", 2))
        cap = int(self.settings.get("max_backoff_seconds", 60))
        payload = None
        for attempt in range(maximum + 1):
            self.check_cancelled()
            try:
                self.network_requests += 1
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.load(response)
                break
            except urllib.error.HTTPError as error:
                if error.code == 403:
                    try:
                        detail = json.loads(error.read().decode("utf-8", "replace"))
                    except (ValueError, OSError):
                        detail = {}
                    detail_remaining = detail.get("remaining_monthly_allowance")
                    detail_extra = int(detail.get("extra_allowance", 0) or 0)
                    if detail_remaining is not None and int(detail_remaining or 0) + detail_extra <= 0:
                        self._pause_for_allowance(detail)
                    self._save_state()
                    raise RuntimeError("TheGamesDB key is invalid or access was denied")
                if error.code not in (408, 429, 500, 502, 503, 504):
                    self._save_state()
                    raise RuntimeError("TheGamesDB HTTP error: %s" % error.code)
                if attempt >= maximum:
                    self._pause_for_service("HTTP %s" % error.code)
                retry_header = error.headers.get("Retry-After") if error.headers else None
                try:
                    delay = min(cap, max(1, int(retry_header)))
                except (TypeError, ValueError):
                    delay = min(cap, base * (2 ** attempt))
            except (urllib.error.URLError, TimeoutError, ValueError) as error:
                if attempt >= maximum:
                    self._pause_for_service(error)
                delay = min(cap, base * (2 ** attempt))
            self.emit("scraper-status", "TheGamesDB unavailable; retrying in %ds" % delay)
            self._wait(delay)
        self.allowance = payload.get("remaining_monthly_allowance", self.allowance)
        self.extra_allowance = int(payload.get("extra_allowance", self.extra_allowance) or 0)
        self.refresh_timer = payload.get("allowance_refresh_timer", self.refresh_timer)
        if payload.get("code", 200) != 200:
            self._save_state()
            raise RuntimeError("TheGamesDB returned: %s" % payload.get("status", "unknown error"))
        if self.usable_allowance() == 0:
            self._pause_for_allowance(payload)
        self._save_state()
        if cache_path:
            try:
                durable_json(cache_path, payload)
            except OSError:
                pass
        return payload

    def _platform_id(self):
        payload = self._request("/v1/Platforms", {}, "platforms.json")
        platforms = payload.get("data", {}).get("platforms", {})
        aliases = PLATFORM_NAMES.get(self.system, (self.profile.get("name", self.system),))
        wanted = {str(name).casefold(): str(name) for name in aliases if name}
        for value in platforms.values():
            name = str(value.get("name", ""))
            alias = wanted.get(name.casefold())
            if alias and value.get("id") is not None:
                return {"id": str(value["id"]), "name": name, "match": "exact", "alias": alias}
        fuzzy = []
        for value in platforms.values():
            name = str(value.get("name", ""))
            folded = name.casefold()
            if any(item in folded or folded in item for item in wanted):
                fuzzy.append(name)
        detail = "No exact TheGamesDB platform match for %s" % self.profile.get("name", self.system)
        if fuzzy:
            detail += "; possible matches: %s" % ", ".join(sorted(fuzzy)[:3])
        raise RuntimeError(detail)

    def _artwork_target(self, path, extension):
        relative = path.relative_to(self.rom_dir)
        parent = relative.parent
        filename = relative.stem + extension
        return self.rom_dir / "images" / parent / filename

    def _rom_identity(self, path):
        suffix = path.suffix.lower()
        if suffix in ARCHIVES:
            return None
        if suffix == ".zip":
            try:
                with zipfile.ZipFile(path) as archive:
                    members = [item for item in archive.infolist() if not item.is_dir() and Path(item.filename).suffix.lower() not in IGNORED]
                    if len(members) == 1:
                        item = members[0]
                        return "crc", "%08x" % item.CRC
            except (OSError, zipfile.BadZipFile):
                return None
            return None
        try:
            if path.stat().st_size > int(self.settings.get("max_hash_mib", 128)) * 1024 * 1024:
                return None
        except OSError:
            return None
        crc = 0
        try:
            with path.open("rb") as stream:
                while True:
                    self.check_cancelled()
                    block = stream.read(1024 * 1024)
                    if not block:
                        break
                    crc = binascii.crc32(block, crc)
            return "crc", "%08x" % (crc & 0xffffffff)
        except OSError:
            return None

    @staticmethod
    def _games(payload):
        return payload.get("data", {}).get("games", []) or []

    def _lookup(self, path, platform_id):
        identity = self._rom_identity(path)
        if identity:
            hash_type, value = identity
            cache_name = "games/%s/hash-%s-%s.json" % (self.system, hash_type, value.lower())
            params = {"hash": value, "filter[type]": hash_type, "fields": "players,publishers,developers,genres,overview,platform", "include": "boxart"}
            if platform_id:
                params["filter[platform]"] = platform_id
            payload = self._request("/v1/Games/ByGameHash", params, cache_name)
            games = self._games(payload)
            if games:
                return games[0], payload, "hash"
        title = self.clean_title(path.name)
        cache_key = hashlib.sha1((self.system + "\0" + title.casefold()).encode()).hexdigest()
        params = {"name": title, "mode": "natural", "fields": "players,publishers,developers,genres,overview,platform", "include": "boxart"}
        if platform_id:
            params["filter[platform]"] = platform_id
        payload = self._request("/v1.1/Games/ByGameName", params, "games/%s/name-%s.json" % (self.system, cache_key))
        games = self._games(payload)
        if not games:
            return None, payload, "name"
        exact = [game for game in games if str(game.get("game_title", "")).casefold() == title.casefold()]
        return (exact[0] if exact else None), payload, "name"

    def _reference_names(self):
        references = {}
        for endpoint, key in (("/v1/Genres", "genres"), ("/v1/Publishers", "publishers"), ("/v1/Developers", "developers")):
            try:
                payload = self._request(endpoint, {}, "references/%s.json" % key)
                references[key] = {str(item_id): value.get("name", str(item_id)) for item_id, value in payload.get("data", {}).get(key, {}).items()}
            except ProviderPaused:
                raise
            except RuntimeError as error:
                self.emit("scraper-log", "Reference data unavailable: %s" % error)
                references[key] = {}
        return references

    @staticmethod
    def _unique_names(values):
        output = []
        seen = set()
        for value in values:
            text = str(value)
            key = text.casefold()
            if text and key not in seen:
                seen.add(key)
                output.append(text)
        return output

    @staticmethod
    def _boxart(payload, game_id):
        boxart = payload.get("include", {}).get("boxart", {})
        bases = boxart.get("base_url", {})
        base = bases.get("small") or bases.get("medium") or bases.get("original") or ""
        images = boxart.get("data", {}).get(str(game_id), [])
        front = next((item for item in images if item.get("type") == "boxart" and item.get("side") == "front"), None)
        return base + front.get("filename", "") if base and front else ""

    def _download(self, url, target):
        if not url:
            return "", False
        target.parent.mkdir(parents=True, exist_ok=True)
        work = Path("/tmp/retroscrape-artwork")
        work.mkdir(parents=True, exist_ok=True)
        temporary = work / (hashlib.sha1((url + str(target)).encode()).hexdigest() + ".download")
        processed = work / (temporary.stem + target.suffix)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        maximum = int(self.settings.get("max_retries", 4))
        base = int(self.settings.get("backoff_seconds", 2))
        cap = int(self.settings.get("max_backoff_seconds", 60))
        for attempt in range(maximum + 1):
            self.check_cancelled()
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response, temporary.open("wb") as output:
                    shutil.copyfileobj(response, output, 256 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
                try:
                    with Image.open(str(temporary)) as source_image:
                        source_image.load()
                        image = ImageOps.exif_transpose(source_image).convert("RGB")
                        image.thumbnail(ARTWORK_MAX_SIZE, RESAMPLE_LANCZOS)
                        image.save(str(processed), format="JPEG", quality=88, optimize=True)
                    content = processed.read_bytes()
                except (OSError, ValueError):
                    temporary.unlink(missing_ok=True)
                    processed.unlink(missing_ok=True)
                    return "", True
                temporary.unlink(missing_ok=True)
                processed.unlink(missing_ok=True)
                write_bytes_if_changed(target.with_suffix(".jpg"), content)
                target = target.with_suffix(".jpg")
                try:
                    display = "./" + str(target.relative_to(self.rom_dir)).replace(os.sep, "/")
                except ValueError:
                    display = str(target)
                return display, False
            except urllib.error.HTTPError as error:
                if error.code not in (408, 429, 500, 502, 503, 504) or attempt >= maximum:
                    temporary.unlink(missing_ok=True)
                    processed.unlink(missing_ok=True)
                    return "", True
                retry_header = error.headers.get("Retry-After") if error.headers else None
                try:
                    delay = min(cap, max(1, int(retry_header)))
                except (TypeError, ValueError):
                    delay = min(cap, base * (2 ** attempt))
            except (OSError, urllib.error.URLError, TimeoutError):
                if attempt >= maximum:
                    temporary.unlink(missing_ok=True)
                    processed.unlink(missing_ok=True)
                    return "", True
                delay = min(cap, base * (2 ** attempt))
            temporary.unlink(missing_ok=True)
            processed.unlink(missing_ok=True)
            self.emit("scraper-status", "Artwork unavailable; retrying in %ds" % delay)
            self._wait(delay)
        return "", True

    def _existing_gamelist(self):
        path = self.rom_dir / "gamelist.xml"
        if not path.exists():
            return ET.Element("gameList"), path
        try:
            root = ET.parse(str(path)).getroot()
            if root.tag != "gameList":
                raise ValueError("unexpected root")
            return root, path
        except (OSError, ET.ParseError, ValueError) as error:
            raise RuntimeError("Existing gamelist.xml is invalid: %s" % error)

    @staticmethod
    def _set(parent, name, value):
        if value in (None, "", []):
            return
        element = parent.find(name)
        if element is None:
            element = ET.SubElement(parent, name)
        element.text = str(value)

    def _write_gamelist(self, records):
        root, path = self._existing_gamelist()
        existing = {item.findtext("path", ""): item for item in root.findall("game")}
        for record in records:
            game = existing.get(record["path"])
            if game is None:
                game = ET.SubElement(root, "game")
                existing[record["path"]] = game
            for key in ("path", "name", "desc", "releasedate", "developer", "publisher", "genre", "players", "image"):
                self._set(game, key, record.get(key))
        temporary = path.with_suffix(".xml.tmp")
        ET.ElementTree(root).write(str(temporary), encoding="utf-8", xml_declaration=True)
        ET.parse(str(temporary))
        content = temporary.read_bytes()
        temporary.unlink(missing_ok=True)
        try:
            unchanged = path.is_file() and path.read_bytes() == content
        except OSError:
            unchanged = False
        if unchanged:
            return False
        if path.exists():
            backup = path.with_suffix(".xml.bak")
            write_bytes_if_changed(backup, path.read_bytes())
        return write_bytes_if_changed(path, content)

    def _storage_preflight(self, files):
        try:
            free = shutil.disk_usage(str(self.rom_dir)).free
        except OSError as error:
            raise RuntimeError("Artwork storage could not be checked: %s" % error)
        pending = sum(1 for path in files if not self._artwork_target(path, ".jpg").is_file())
        estimated = pending * 512 * 1024
        required = estimated + 32 * 1024 * 1024
        details = {"free_bytes": free, "estimated_bytes": estimated, "pending_artwork": pending}
        self.emit("scraper-storage", details)
        if free < required:
            raise RuntimeError("Not enough free space for artwork: %d MiB available, about %d MiB required" % (free // (1024 * 1024), required // (1024 * 1024)))
        return details

    def run(self):
        self._read_key()
        self._load_state()
        now = int(time.time())
        if self.retry_after > now:
            remaining = self.retry_after - now
            return {
                "provider": "TheGamesDB",
                "status": "Paused",
                "pause_reason": self.state.get("pause_reason", "retry"),
                "retry_after": self.retry_after,
                "retry_in_seconds": remaining,
                "matched": 0,
                "total": 0,
                "remaining_monthly_allowance": self.state.get("remaining_monthly_allowance"),
            }
        if self.retry_after:
            self.retry_after = 0
            self.state.pop("pause_reason", None)
            self._save_state()
        if not self.rom_dir.is_dir():
            raise RuntimeError("ROM folder not found: %s" % self.rom_dir)
        files = sorted(path for path in iter_processable_files(self.rom_dir) if path.suffix.lower() not in ARCHIVES)
        if not files:
            return {"provider": "TheGamesDB", "status": "Not applicable: no scrapeable files", "matched": 0, "total": 0, "processed": 0, "not_found": 0, "lookup_failures": 0, "media_pending_details": {}}

        storage = self._storage_preflight(files)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.emit("scraper-status", "Connecting to TheGamesDB")
        platform = self._platform_id()
        platform_id = platform["id"]
        references = self._reference_names()
        retroarch = self._retroarch_context()
        ra_entries = retroarch.get("entries", {})
        ra_mapping = retroarch.get("mapping", {})
        collection = str(self.rom_dir.resolve())
        current = {}
        for path in files:
            relative = "./" + str(path.relative_to(self.rom_dir)).replace(os.sep, "/")
            try:
                stat = path.stat()
                identity = "%d:%d" % (stat.st_size, int(stat.st_mtime))
            except OSError:
                identity = "missing"
            current[relative] = (path, identity)

        systems = self.state.setdefault("systems", {})
        progress = systems.get(self.system)
        if not isinstance(progress, dict) or progress.get("rom_dir") != collection:
            progress = {
                "rom_dir": collection,
                "completed": {},
                "records": [],
                "media_pending": {},
                "not_found": [],
                "lookup_failures": {},
            }
            systems[self.system] = progress

        raw_completed = progress.get("completed", {})
        completed = {
            relative: identity
            for relative, identity in (raw_completed.items() if isinstance(raw_completed, dict) else [])
            if relative in current
        }
        media_pending = {
            relative: pending
            for relative, pending in progress.get("media_pending", {}).items()
            if relative in current and isinstance(pending, dict)
        }
        not_found = {relative for relative in progress.get("not_found", []) if relative in current}
        raw_failures = progress.get("lookup_failures", {})
        lookup_failures = {relative: str(error) for relative, error in (raw_failures.items() if isinstance(raw_failures, dict) else []) if relative in current}

        record_map = {}
        for record in progress.get("records", []):
            if not isinstance(record, dict):
                continue
            relative = record.get("path", "")
            if relative in current:
                record_map[relative] = record

        progress["rom_dir"] = collection
        progress["completed"] = completed
        progress["records"] = list(record_map.values())
        progress["media_pending"] = media_pending
        progress["not_found"] = sorted(not_found)
        progress["lookup_failures"] = lookup_failures
        progress["total"] = len(files)
        self._save_state()

        ra_skipped = 0
        ra_written = 0
        ra_unmapped = 0
        reserve = int(self.settings.get("reserve_requests", 10))
        for index, path in enumerate(files, 1):
            self.check_cancelled()
            relative = "./" + str(path.relative_to(self.rom_dir)).replace(os.sep, "/")
            identity = current[relative][1]
            try:
                absolute = str(path.resolve())
            except OSError:
                absolute = str(path.absolute())
            ra_entry = ra_entries.get(absolute)
            ra_collection = ra_mapping.get(ra_entry.get("playlist", "")) if ra_entry else None
            ra_boxart = None
            if ra_entry and ra_collection:
                art_name = self._retroarch_thumbnail_name(ra_entry["label"])
                candidates = [ra_collection / "Named_Boxarts" / (art_name + suffix) for suffix in (".png", ".jpg", ".jpeg", ".webp")]
                ra_boxart = next((candidate for candidate in candidates if candidate.is_file()), None)
                if ra_boxart and relative in record_map and completed.get(relative) != identity:
                    completed[relative] = identity
                    ra_skipped += 1
                    progress["completed"] = completed
                    self._checkpoint_state()
                    self.emit("progress", index, len(files), "Metadata and artwork already present")
                    continue
            elif not ra_entry or not ra_collection:
                ra_unmapped += 1

            if completed.get(relative) == identity and relative not in media_pending:
                self.emit("progress", index, len(files), "Scraping games")
                continue

            if relative in media_pending:
                pending = media_pending[relative]
                image, still_pending = self._download(
                    pending.get("url", ""), Path(pending.get("target", ""))
                )
                if not still_pending:
                    if relative in record_map:
                        record_map[relative]["image"] = image
                    media_pending.pop(relative, None)
                    completed[relative] = identity
                progress["records"] = list(record_map.values())
                progress["media_pending"] = media_pending
                progress["completed"] = completed
                self._checkpoint_state()
                self.emit("progress", index, len(files), "Retrying artwork")
                continue

            available = self.usable_allowance()
            if available is not None and available <= reserve:
                self._pause_for_allowance({"remaining_monthly_allowance": self.allowance, "extra_allowance": self.extra_allowance, "allowance_refresh_timer": self.refresh_timer})

            self.emit("scraper-status", "TheGamesDB %d/%d" % (index, len(files)))
            lookup_finished = False
            try:
                game, payload, method = self._lookup(path, platform_id)
                lookup_finished = True
                lookup_failures.pop(relative, None)
                if not game:
                    self.emit("scraper-log", "No match: %s" % path.name)
                    record_map.pop(relative, None)
                    media_pending.pop(relative, None)
                    not_found.add(relative)
                else:
                    game_id = game.get("id")
                    image_url = self._boxart(payload, game_id)
                    image = ""
                    if self.settings.get("download_boxart", True) and image_url:
                        extension = Path(urllib.parse.urlparse(image_url).path).suffix or ".jpg"
                        target = self._artwork_target(path, extension)
                        image, pending = self._download(image_url, target)
                        if pending:
                            media_pending[relative] = {"url": image_url, "target": str(target)}
                        else:
                            media_pending.pop(relative, None)
                            if ra_entry and ra_collection and image:
                                source_image = self.rom_dir / image.lstrip("./")
                                ra_target = ra_collection / "Named_Boxarts" / self._retroarch_thumbnail_name(ra_entry["label"])
                                try:
                                    self._publish_retroarch_boxart(source_image, ra_target)
                                    ra_written += 1
                                except (OSError, ValueError) as error:
                                    self.emit("scraper-log", "RetroArch artwork not written: %s" % error)
                    publishers = self._unique_names(references["publishers"].get(str(value), str(value)) for value in (game.get("publishers") or []))
                    genres = self._unique_names(references["genres"].get(str(value), str(value)) for value in (game.get("genres") or []))
                    developers = self._unique_names(references["developers"].get(str(value), str(value)) for value in (game.get("developers") or []))
                    release = str(game.get("release_date") or "").replace("-", "")
                    release = (release + "T000000") if len(release) == 8 else ""
                    record_map[relative] = {
                        "path": relative,
                        "name": game.get("game_title") or self.clean_title(path.name),
                        "desc": game.get("overview") or "",
                        "releasedate": release,
                        "developer": ", ".join(developers),
                        "publisher": ", ".join(publishers),
                        "genre": ", ".join(genres),
                        "players": game.get("players"),
                        "image": image,
                        "lookup": method,
                    }
                    not_found.discard(relative)
            except ProviderPaused:
                raise
            except RuntimeError as error:
                lookup_failures[relative] = str(error)
                self.emit("scraper-log", "%s: %s" % (path.name, error))

            if lookup_finished:
                completed[relative] = identity
            progress["completed"] = completed
            progress["records"] = list(record_map.values())
            progress["media_pending"] = media_pending
            progress["not_found"] = sorted(not_found)
            progress["lookup_failures"] = lookup_failures
            self._checkpoint_state()
            self.emit("progress", index, len(files), "Scraping games")

        self.check_cancelled()
        records = [record_map[path] for path in sorted(record_map)]
        matched = len(records)
        if matched > len(files):
            raise RuntimeError("Internal scraper count is inconsistent")
        if records:
            self._write_gamelist(records)

        try:
            final_root, _ = self._existing_gamelist()
            total_gamelist_entries = len(final_root.findall("game"))
        except RuntimeError:
            total_gamelist_entries = 0

        if media_pending or lookup_failures:
            progress["records"] = records
            progress["media_pending"] = media_pending
            progress["lookup_failures"] = lookup_failures
            if lookup_failures:
                status = "Complete with %d lookup failures: %d/%d matched" % (len(lookup_failures), matched, len(files))
            else:
                status = "Complete with %d artwork pending: %d/%d matched" % (len(media_pending), matched, len(files))
        else:
            systems.pop(self.system, None)
            status = ("Complete with %d unmatched: %d/%d matched" % (len(not_found), matched, len(files))) if not_found else ("Complete: %d/%d matched" % (matched, len(files)))
        self.retry_after = 0
        self.state.pop("pause_reason", None)
        self._save_state()
        self.emit("scraper-status", status)
        return {
            "provider": "TheGamesDB",
            "status": status,
            "matched": matched,
            "total": len(files),
            "processed": len(completed),
            "not_found": len(not_found),
            "not_found_details": sorted(not_found),
            "total_gamelist_entries": total_gamelist_entries,
            "retroarch_config": retroarch.get("config", ""),
            "retroarch_thumbnail_root": str(retroarch.get("thumbnail_root", "")),
            "retroarch_boxart_written": ra_written,
            "retroarch_boxart_already_present": ra_skipped,
            "retroarch_unmapped_roms": ra_unmapped,
            "remaining_monthly_allowance": self.allowance,
            "extra_allowance": self.extra_allowance,
            "total_available_allowance": self.usable_allowance(),
            "lookup_failures": len(lookup_failures),
            "lookup_failure_details": dict(lookup_failures),
            "media_pending_details": dict(media_pending),
            "platform": dict(platform),
            "period_api_calls": int(self.state.get("usage_period", {}).get("network_requests", 0) or 0),
            "period_cache_hits": int(self.state.get("usage_period", {}).get("cache_hits", 0) or 0),
            "lifetime_api_calls": int(self.state.get("usage", {}).get("network_requests", 0) or 0),
            "lifetime_cache_hits": int(self.state.get("usage", {}).get("cache_hits", 0) or 0),
            "local_network_requests": int(self.state.get("usage", {}).get("network_requests", 0) or 0),
            "local_cache_hits": int(self.state.get("usage", {}).get("cache_hits", 0) or 0),
            "gamelist": str(self.rom_dir / "gamelist.xml") if records else "",
            "storage_preflight": storage,
        }
