import json
import re
import time
from pathlib import Path

from .storage import durable_json

HASH_PATTERN = re.compile(r"\b(?:crc|md5|sha1)\s+(?:\"[^\"]+\"|[^\s\)]+)", re.IGNORECASE)
ROM_PATTERN = re.compile(r"\brom\s*\(", re.IGNORECASE)

IGNORED_CONTENT_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".xml", ".json", ".txt", ".log",
    ".cfg", ".ini", ".sav", ".srm", ".state", ".mp4", ".pdf", ".tmp",
    ".bak", ".py", ".pyc", ".md",
}
IGNORED_CONTENT_DIRECTORIES = {
    "images", "image", "screenshots", "screenshot", "boxart", "boxarts",
    "named_boxarts", "named_snaps", "named_titles", "named_logos", "__pycache__",
}

def is_processable_file(path, root=None):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        return False
    if path.name.startswith(".") or path.name.casefold() in ("gamelist.xml", "desktop.ini", "thumbs.db"):
        return False
    if root is not None:
        try:
            relative = path.relative_to(Path(root))
        except ValueError:
            return False
        if any(part.casefold() in IGNORED_CONTENT_DIRECTORIES or part.startswith(".") for part in relative.parts[:-1]):
            return False
    return path.suffix.casefold() not in IGNORED_CONTENT_SUFFIXES

def iter_processable_files(folder):
    folder = Path(folder)
    if not folder.is_dir():
        return
    try:
        for path in folder.rglob("*"):
            if is_processable_file(path, folder):
                yield path
    except OSError:
        return

def has_processable_content(folder):
    return next(iter_processable_files(folder), None) is not None

def content_marker(folder):
    folder = Path(folder)
    if not folder.is_dir():
        return (str(folder), "missing", "")
    try:
        stamp = int(folder.stat().st_mtime)
    except OSError:
        stamp = 0
    first = ""
    try:
        for path in iter_processable_files(folder):
            first = str(path.relative_to(folder))
            break
    except OSError:
        pass
    return (str(folder), stamp, first)

SPECIALISED_DAT_MARKERS = (
    "virtual console",
    "nintendo classic mini",
    "super nintendo classic edition",
    "snes mini",
)

def specialised_dat_reason(path):
    """Return a reason when a downloaded DAT describes a restricted subset."""
    try:
        with Path(path).open("r", encoding="utf-8", errors="ignore") as handle:
            header = handle.read(256 * 1024).casefold()
    except OSError as error:
        return str(error)
    marker = next((value for value in SPECIALISED_DAT_MARKERS if value in header), "")
    return "specialised subset: %s" % marker if marker else ""


def detect_rom_folder(config, profile):
    for root_value in config.get("rom_roots", []):
        root = Path(root_value)
        if not root.is_dir():
            continue
        directories = {path.name.casefold(): path for path in root.iterdir() if path.is_dir()}
        for folder in profile.get("folders", []):
            direct = root / folder
            if direct.is_dir():
                return direct
            matched = directories.get(folder.casefold())
            if matched:
                return matched
    return None


def dat_candidates(app_dir, profile, system):
    app_dir = Path(app_dir)
    pattern = re.compile(profile.get("dat", r"$a"), re.IGNORECASE)
    downloaded = []
    current = app_dir / "dats" / "current"
    if current.exists():
        downloaded = [path for path in current.rglob("*.dat") if pattern.fullmatch(path.name)]
    local_dir = app_dir / "dats" / "local" / system
    local = list(local_dir.rglob("*.dat")) if local_dir.exists() else []
    return sorted(local), sorted(downloaded)


def inspect_dat(path):
    path = Path(path)
    try:
        if path.stat().st_size < 32:
            return False, "empty or truncated"
        found_rom = False
        found_hash = False
        tail = ""
        scanned = 0
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            while scanned < 8 * 1024 * 1024 and not (found_rom and found_hash):
                chunk = handle.read(256 * 1024)
                if not chunk:
                    break
                scanned += len(chunk)
                sample = tail + chunk
                found_rom = found_rom or bool(ROM_PATTERN.search(sample))
                found_hash = found_hash or bool(HASH_PATTERN.search(sample))
                tail = sample[-4096:]
        if not found_rom:
            return False, "no ROM records in first 8 MiB"
        if not found_hash:
            return False, "no supported hashes in first 8 MiB"
        return True, ""
    except OSError as error:
        return False, str(error)


def source_fingerprint(app_dir, config):
    app_dir = Path(app_dir)
    records = []
    for base in (app_dir / "dats" / "current", app_dir / "dats" / "local"):
        if base.exists():
            for path in sorted(base.rglob("*.dat")):
                try:
                    stat = path.stat()
                    records.append((str(path.relative_to(app_dir)), stat.st_size, int(stat.st_mtime)))
                except OSError:
                    pass
    for value in config.get("rom_roots", []):
        root = Path(value)
        if root.is_dir():
            try:
                directories = sorted((path for path in root.iterdir() if path.is_dir()), key=lambda item: item.name.casefold())
                records.append((str(root), tuple((path.name.casefold(), content_marker(path)) for path in directories)))
            except OSError:
                pass
    return repr(records)

def xmame_database_status(config):
    settings = config.get("xmame", {})
    path = Path(settings.get("xml_path", ""))
    expected = str(settings.get("expected_build", ""))
    if not path.is_file():
        return "needs-generation", "MAME database will be generated when scanning starts"
    try:
        if path.stat().st_size <= 1024:
            return "invalid", "MAME database is empty or truncated and will be rebuilt"
        with path.open("rb") as handle:
            head = handle.read(4 * 1024 * 1024)
    except OSError as error:
        return "invalid", "MAME database could not be read: %s" % error
    pattern = re.compile(br'<mame\s+[^>]*build=["\']([^"\']+)["\']', re.IGNORECASE)
    match = pattern.search(head)
    build = match.group(1).decode("utf-8", "replace") if match else ""
    if build != expected:
        return "invalid", "MAME database build is %s; expected %s" % (build or "unknown", expected)
    return "ready", "MAME database is ready"

def build_index(app_dir, config, systems):
    app_dir = Path(app_dir)
    entries = {}
    scraping_enabled = bool(config.get("thegamesdb", {}).get("enabled"))
    for key, profile in systems.items():
        rom_folder = detect_rom_folder(config, profile)
        folder_detected = bool(rom_folder)
        content_detected = bool(rom_folder and has_processable_content(rom_folder))
        common = {
            "name": profile.get("name", key),
            "rom_folder": str(rom_folder) if rom_folder else "",
            "folder_detected": folder_detected,
            "content_detected": content_detected,
            "detected": False,
        }
        if not folder_detected:
            entries[key] = dict(common, validation=False, provider=profile.get("provider", "generic"), capability="Unavailable", action="Unavailable", validation_status="folder-missing", validation_detail="ROM folder not found", validator="", selected_dat="", alternatives=[], errors=[])
            continue
        if not content_detected:
            entries[key] = dict(common, validation=False, provider=profile.get("provider", "generic"), capability="Unavailable", action="Unavailable", validation_status="empty-folder", validation_detail="No processable game files found", validator="", selected_dat="", alternatives=[], errors=[])
            continue
        if profile.get("provider") == "xmame":
            executable = Path(config["xmame"]["executable"])
            xml_path = Path(config["xmame"]["xml_path"])
            if executable.is_file():
                validation = True
                database_status, database_detail = xmame_database_status(config)
                error = "" if database_status in ("ready", "needs-generation") else database_detail
            else:
                validation = False
                database_status = "unavailable"
                database_detail = "xMAME executable not found"
                error = database_detail
            actionable = validation or scraping_enabled
            action = "Validate + scrape" if validation and scraping_enabled else ("Validate only" if validation else ("Scrape only" if scraping_enabled else "Unavailable"))
            entries[key] = dict(common, detected=actionable, validation=validation, provider="xmame", capability=action, action=action, validator="xMAME 0.106", validation_status=("available" if validation else "error"), validation_detail=database_detail, database_status=database_status, database_detail=database_detail, selected_dat="", alternatives=[], error=error, errors=([error] if error else []))
            continue
        local, downloaded = dat_candidates(app_dir, profile, key)
        valid_local, valid_downloaded, errors = [], [], []
        for path in local:
            valid, error = inspect_dat(path)
            (valid_local if valid else errors).append(str(path) if valid else "%s: %s" % (path.name, error))
        for path in downloaded:
            specialised = specialised_dat_reason(path)
            valid, error = inspect_dat(path)
            if valid and specialised:
                valid = False
                error = specialised
            (valid_downloaded if valid else errors).append(str(path) if valid else "%s: %s" % (path.name, error))
        valid_paths = valid_local + valid_downloaded
        selected = valid_paths[0] if valid_paths else ""
        validation = bool(selected)
        action = "Validate + scrape" if validation and scraping_enabled else ("Validate only" if validation else ("Scrape only" if scraping_enabled else "Unavailable"))
        validation_status = "available" if validation else ("error" if errors else "unavailable")
        validation_detail = errors[0] if errors else ("No matching validation DAT found" if not validation else "")
        entries[key] = dict(common, detected=(validation or scraping_enabled), validation=validation, provider="generic", capability=action, action=action, validator="DAT", validation_status=validation_status, validation_detail=validation_detail, selected_dat=selected, alternatives=valid_paths[1:], errors=errors)
    path = app_dir / "data" / "database-index.json"
    fingerprint = source_fingerprint(app_dir, config)
    created = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
        if previous.get("schema") == 5 and previous.get("fingerprint") == fingerprint and previous.get("systems") == entries:
            created = previous.get("created", created)
    except (OSError, ValueError, TypeError):
        pass
    payload = {"schema": 5, "created": created, "fingerprint": fingerprint, "systems": entries}
    try:
        durable_json(path, payload)
    except OSError:
        payload["cache_status"] = "not saved"
    return payload
def load_or_build(app_dir, config, systems):
    path = Path(app_dir) / "data" / "database-index.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") == 5 and set(data.get("systems", {})) == set(systems) and data.get("fingerprint") == source_fingerprint(app_dir, config):
            return data
    except Exception:
        pass
    return build_index(app_dir, config, systems)
