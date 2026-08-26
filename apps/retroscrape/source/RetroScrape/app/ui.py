import ctypes
import ctypes.util
import json
import queue
import sys
import threading
import traceback
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .storage import durable_json
from .xmame import ArcadeOrganiser, Cancelled, XMameValidator
from .generic import BiosValidator, DatValidator
from .thegamesdb import ProviderPaused, TheGamesDBScraper
from .reports import issue_lines, save_report
from .database_index import build_index, has_processable_content, load_or_build, xmame_database_status
from .updater import DatabaseUpdater

W, H = 640, 480
INIT_VIDEO, INIT_JOY, FULL, POS = 0x20, 0x200, 1, 0x1FFF0000
ACC, VSYNC, SOFT, QUIT, BDOWN, HAT = 2, 4, 1, 0x100, 0x603, 0x602
A, B, Y, X, L1, R1, MENU_HOLD, MENU, UP, RIGHT, DOWN, LEFT = 0, 1, 2, 3, 4, 5, 8, 13, 1, 2, 4, 8
LAYOUT_LEFT, LAYOUT_RIGHT = 24, 616
LAYOUT_FOOTER_Y, LAYOUT_NOTICE_Y = 454, 431
MENU_ROW_LEFT, MENU_ROW_RIGHT, MENU_ROW_HEIGHT = 36, 604, 56
MENU_ICON_X, MENU_ICON_SIZE, MENU_TEXT_X = 48, 40, 98
MENU_ICONS = {"Scan Games":"scan", "Scan BIOS":"bios", "Last Report":"report", "Settings":"settings", "Game scraping":"scrape", "TheGamesDB Usage":"cache", "Update Databases":"update", "Rebuild MAME Database":"mame", "Advanced":"settings", "Show unavailable systems":"visibility", "Refresh Systems":"refresh", "Database Status":"database", "Remove Database Backup":"storage", "Back":"back"}
SYSTEM_ICON_TYPES = {
    "amiga": "keyboard", "gamecube": "cube", "nes": "frontload", "snes": "cart",
    "wii": "tower", "wiiu": "tablet", "neogeo": "arcadestick", "saturn": "disc",
    "mame": "cabinet", "gb": "handheld_v", "gbc": "handheld_round", "gba": "handheld_h",
    "n64": "curved_cart", "nds": "dual", "dreamcast": "disc_ports", "genesis": "oval_cart",
    "mastersystem": "flat_cart", "gamegear": "wide_handheld", "ps1": "disc_ports2",
    "ps2": "stacked", "psp": "slim_handheld",
}
SYSTEM_ICON_COLOURS = {
    "amiga": (195, 78, 90), "gamecube": (107, 78, 180), "nes": (190, 62, 62),
    "snes": (112, 91, 171), "wii": (55, 165, 210), "wiiu": (35, 140, 205),
    "neogeo": (215, 155, 50), "saturn": (70, 95, 170), "mame": (55, 145, 120),
    "gb": (92, 120, 74), "gbc": (115, 86, 175), "gba": (64, 110, 175),
    "n64": (65, 145, 95), "nds": (120, 130, 145), "dreamcast": (225, 105, 55),
    "genesis": (55, 75, 145), "mastersystem": (205, 70, 70), "gamegear": (55, 90, 145),
    "ps1": (85, 95, 110), "ps2": (45, 75, 145), "psp": (65, 90, 145),
}

class GUI:
    def __init__(self, app, config, systems, warning=None):
        self.app = Path(app)
        self.config = config
        self.systems = systems
        self.database_index = {"systems": {}}
        self.system_keys = list(self.systems)
        self.startup_warning = warning
        self.startup_started = False
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.running = False
        self.operation = "startup"
        self.page = "startup"
        self.selection = 0
        self.home = ["Scan Games", "Scan BIOS", "Last Report", "Settings"]
        self.settings = ["Game scraping", "TheGamesDB Usage", "Update Databases", "Rebuild MAME Database", "Advanced", "Back"]
        self.advanced = ["Show unavailable systems", "Refresh Systems", "Database Status", "Remove Database Backup", "Back"]
        self.menu_scroll = 0
        self.menu_rows = 5
        self.menu_selections = {"home": 0, "settings": 0, "advanced": 0}
        self.system_selection = 0
        self.system_scroll = 0
        self.system_rows = 6
        self.logs = [warning] if warning else []
        self.summary = {}
        self.progress = (0, 0, "")
        self.stage = "Ready"
        self.validation = "Idle"
        self.pending_report = None
        self.report_data = None
        self.report_path = None
        self.report_paths = []
        self.report_index = 0
        self.pending_bios_validator = None
        self.prompt_yes = True
        self.issue_items = []
        self.issue_scroll = 0
        self.issue_rows = 8
        self.organisation = None
        self.organisation_items = []
        self.organisation_scroll = 0
        self.organisation_rows = 7
        self.organisation_result = None
        self.scraper_status = "Idle"
        self.scraper_exit_code = None
        self.api_status = None
        self.api_status_error = ""
        self.api_status_message = ""
        self.api_status_loading = False
        self.detail_title = ""
        self.detail_rows = []
        self.detail_text = ""
        self.detail_back = "home"
        self.detail_scroll = 0
        self.detail_rows_visible = 8
        self.notice_text = ""
        self.notice_kind = "info"
        self.notice_until = 0.0
        self._font_cache = {}
        self.icon_sheet = None
        self.icon_cells = {}
        self.system_badges = {}
        self.joystick_name = ""
        self._load_icons()
        self.dirty = True

    @staticmethod
    def _format_bytes(value):
        try:
            size = float(value)
        except (TypeError, ValueError):
            return "0 B"
        for unit in ("B", "KiB", "MiB", "GiB"):
            if size < 1024 or unit == "GiB":
                return ("%.1f %s" % (size, unit)) if unit != "B" else ("%d B" % size)
            size /= 1024.0

    @staticmethod
    def _tree_stats(path, pattern="*"):
        path = Path(path)
        if not path.is_dir():
            return 0, 0
        count = 0
        size = 0
        try:
            for item in path.rglob(pattern):
                if item.is_file():
                    count += 1
                    size += item.stat().st_size
        except OSError:
            pass
        return count, size

    def show_database_status(self):
        dats = self.app / "dats"
        current_count, current_size = self._tree_stats(dats / "current", "*.dat")
        backup_count, backup_size = self._tree_stats(dats / ".previous", "*.dat")
        journal_phase = "None"
        try:
            journal = json.loads((dats / ".update-state.json").read_text(encoding="utf-8"))
            journal_phase = str(journal.get("phase", "Unknown"))
        except (OSError, ValueError):
            pass
        manifest = {}
        try:
            manifest = json.loads((dats / "current" / ".retroscrape-manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        mame_status, mame_detail = xmame_database_status(self.config)
        self.detail_title = "Database Status"
        self.detail_rows = [
            ("Current DAT files", current_count),
            ("Current size", self._format_bytes(current_size)),
            ("Backup DAT files", backup_count),
            ("Backup size", self._format_bytes(backup_size)),
            ("Update journal", journal_phase),
            ("Downloaded", manifest.get("downloaded_at", "Not recorded")),
            ("Source DAT files", manifest.get("dat_count", "Not recorded")),
            ("Installed payload", self._format_bytes(manifest.get("total_size", 0)) if manifest.get("total_size") is not None else "Not recorded"),
            ("MAME database", mame_status.replace("-", " ").title()),
        ]
        self.detail_text = mame_detail
        self.detail_back = "advanced"
        self.detail_scroll = 0
        self.page = "details"
        self.dirty = True

    def show_report_details(self):
        data = self.report_data or {}
        scraping = data.get("scraping", {}) if isinstance(data.get("scraping"), dict) else {}
        organisation = data.get("organisation", {}) if isinstance(data.get("organisation"), dict) else {}
        self.detail_title = "Report Details"
        self.detail_rows = [
            ("System", self.systems.get(data.get("system"), {}).get("name", data.get("system") or data.get("type", "Unknown"))),
            ("Saved", data.get("saved_at", "Unknown")),
            ("App version", data.get("app_version", "Unknown")),
            ("Validation items", len(data.get("results", []))),
            ("Scraping", scraping.get("status", "Not recorded")),
            ("Suggestions", organisation.get("summary", {}).get("MOVE_RECOMMENDED", 0)),
        ]
        self.detail_text = str(data.get("source", ""))
        self.detail_back = "report"
        self.detail_scroll = 0
        self.page = "details"
        self.dirty = True

    def start_initialisation(self):
        if self.startup_started:
            return
        self.startup_started = True
        self.page = "startup"
        self.stage = "Checking RetroScrape installation"
        self.progress = (0, 0, "Checking database state")
        self.dirty = True
        def worker():
            warning = self.startup_warning
            try:
                self.emit("startup-stage", "Checking database recovery")
                DatabaseUpdater(self.app, self.config, lambda *event: None, threading.Event()).recover()
                ArcadeOrganiser(self.app, self.config, lambda *event: None, threading.Event()).recover()
                self.emit("startup-stage", "Checking systems and DAT files")
                index = load_or_build(self.app, self.config, self.systems)
                self.emit("startup-complete", index, warning)
            except Exception as error:
                self.emit("startup-failed", str(error), warning)
        threading.Thread(target=worker, daemon=True).start()
    def refresh_system_keys(self):
        entries = self.database_index.get("systems", {})
        if self.config.get("show_missing_systems"):
            keys = list(self.systems)
        else:
            keys = [key for key in self.systems if entries.get(key, {}).get("detected")]
        selected = self.config.get("selected_system")
        self.system_keys = keys
        if self.system_keys and selected not in self.system_keys:
            self.config["selected_system"] = self.system_keys[0]

    def rebuild_database_index(self):
        self.database_index = build_index(self.app, self.config, self.systems)
        self.refresh_system_keys()
        self.system_selection = min(self.system_selection, max(0, len(self.system_keys) - 1))
        self.system_scroll = min(self.system_scroll, max(0, len(self.system_keys) - self.system_rows))
        self.dirty = True

    @staticmethod
    def _duration(value):
        if value is None:
            return "No reset timer reported"
        try:
            seconds = max(0, int(value))
        except (TypeError, ValueError):
            return "Unknown"
        days, seconds = divmod(seconds, 86400)
        hours, minutes = divmod(seconds, 3600)
        if days:
            return "%dd %dh" % (days, hours)
        if hours:
            return "%dh %dm" % (hours, minutes // 60)
        return "%dm" % (minutes // 60)

    @staticmethod
    def _clock(value):
        try:
            return time.strftime("%H:%M", time.localtime(int(value)))
        except (TypeError, ValueError, OSError):
            return "Never"

    @staticmethod
    def _wrap_text(text, limit=76, lines=3):
        words = str(text or "").split()
        output = []
        current = ""
        for word in words:
            candidate = (current + " " + word).strip()
            if len(candidate) > limit and current:
                output.append(current)
                current = word
                if len(output) >= lines:
                    break
            else:
                current = candidate
        if current and len(output) < lines:
            output.append(current)
        return output

    @staticmethod
    def _status_label(value):
        labels = {
            "OK": "OK", "VERIFIED": "Verified", "UNMATCHED": "Unmatched",
            "NO_MATCH": "No match", "LOOKUP_FAILED": "Lookup failed",
            "ARTWORK_PENDING": "Artwork pending", "READ_ERROR": "Read error",
            "CHECKSUM_MISMATCH": "Checksum mismatch", "CASE_MISMATCH": "Case mismatch",
            "MISNAMED": "Wrong name", "MISNAMED_MEMBERS": "Wrong member names",
            "BADPATH": "Wrong path", "RENAME": "Wrong name",
            "BADPATH_RENAME": "Wrong path and name", "DUPLICATE": "Duplicate",
            "INCOMPLETE": "Incomplete", "UNSUPPORTED": "Unsupported",
            "UNSUPPORTED_ARCHIVE": "Unsupported archive", "EMPTY_ARCHIVE": "Empty archive",
            "VERIFIED_NEEDS_PARENT": "Verified, parent required",
            "VERIFIED_WITH_EXTRAS": "Verified with extras",
            "FIX_ERROR": "Fix failed", "DELETE_ERROR": "Delete failed",
            "FIXED": "Fixed", "DELETED_DUPLICATE": "Duplicate deleted",
            "MOVE_RECOMMENDED": "Move recommended", "TARGET_EXISTS": "Target exists",
        }
        text = str(value or "Unknown")
        return labels.get(text, text.replace("_", " ").strip().title())

    @staticmethod
    def _status_colour(value):
        status = str(value or "").upper()
        if status in {"OK", "VERIFIED", "FIXED", "DELETED_DUPLICATE", "COMPLETE", "READY"}:
            return (125, 235, 165)
        if status in {"READ_ERROR", "FIX_ERROR", "DELETE_ERROR", "CHECKSUM_MISMATCH", "FAILED", "ERROR"} or status.endswith("_ERROR"):
            return (255, 125, 115)
        if status in {"UNAVAILABLE", "CANCELLED", "NOT RUN", "NOT RECORDED", "DISABLED"}:
            return (145, 155, 170)
        if status in {"RUNNING", "WAITING", "REFRESHING", "INFORMATION"}:
            return (130, 190, 255)
        return (255, 190, 110)

    def _header_context(self):
        if self.page in ("systems", "system_details"):
            return "Systems"
        if self.page == "report":
            data = self.report_data or {}
            return self.systems.get(data.get("system"), {}).get("name", data.get("system") or data.get("type") or "Reports")
        if self.page == "api_status":
            return "TheGamesDB"
        if self.page == "details":
            return "Databases" if self.detail_title == "Database Status" else ("Reports" if self.detail_title == "Report Details" else "Details")
        if self.page in ("settings", "advanced"):
            return "Settings"
        if self.operation == "bios_scan" and self.page in ("run", "results", "issues", "save_report", "bios_fix_confirm", "bios_duplicate_confirm"):
            return "BIOS"
        if self.page == "startup":
            return "Starting"
        system = self.config.get("selected_system", "")
        return self.systems.get(system, {}).get("name", system)

    def _summary_items(self):
        rank = {"READ_ERROR": 0, "FIX_ERROR": 0, "DELETE_ERROR": 0, "CHECKSUM_MISMATCH": 0,
                "INCOMPLETE": 1, "UNMATCHED": 1, "NO_MATCH": 1, "LOOKUP_FAILED": 1,
                "MISNAMED": 2, "CASE_MISMATCH": 2, "BADPATH": 2, "RENAME": 2,
                "BADPATH_RENAME": 2, "ARTWORK_PENDING": 2, "DUPLICATE": 2,
                "OK": 4, "VERIFIED": 4}
        return sorted(self.summary.items(), key=lambda item: (rank.get(str(item[0]), 3), self._status_label(item[0]).casefold()))

    def show_notice(self, text, kind="info", seconds=4.0):
        self.notice_text = str(text or "")
        self.notice_kind = kind if kind in ("success", "info", "warning", "error", "neutral") else "info"
        self.notice_until = time.monotonic() + max(0.5, float(seconds)) if self.notice_text else 0.0
        self.dirty = True

    def _notice_active(self):
        if not self.notice_text:
            return False
        if time.monotonic() >= self.notice_until:
            self.notice_text = ""
            self.notice_until = 0.0
            self.dirty = True
            return False
        return True

    def _draw_notice(self, draw, y=430):
        if not self._notice_active():
            return
        colours = {"success": (125, 235, 165), "info": (130, 190, 255), "warning": (255, 190, 110), "error": (255, 125, 115), "neutral": (170, 185, 205)}
        font = self.font(11, True)
        draw.text((LAYOUT_LEFT, y), self._fit_text(draw, self.notice_text, font, LAYOUT_RIGHT - LAYOUT_LEFT), font=font, fill=colours[self.notice_kind])

    def start_api_status(self, refresh=True):
        if self.api_status_loading:
            return
        self.page = "api_status"
        self.api_status_error = ""
        self.api_status_message = ""
        scraper = TheGamesDBScraper(self.app, self.config, {}, self.config.get("selected_system", ""), self.app, self.emit, self.stop)
        if not refresh:
            self.api_status = scraper.cached_allowance_status()
            self.dirty = True
            return
        self.api_status_loading = True
        self.api_status = scraper.cached_allowance_status()
        self.dirty = True
        def worker():
            try:
                self.emit("api-status", scraper.allowance_status())
            except Exception as error:
                self.emit("api-status-error", str(error))
        threading.Thread(target=worker, daemon=True).start()

    def start_database_update(self):
        if self.running:
            return
        self.running = True
        self.operation = "database_update"
        self.page = "run"
        self.stop.clear()
        self.logs = []
        self.summary = {}
        self.progress = (0, 0, "")
        self.stage = "Starting database update"
        self.validation = "Database update"
        self.scraper_status = "Not used"
        self.dirty = True
        def worker():
            try:
                DatabaseUpdater(self.app, self.config, self.emit, self.stop).run()
                index = build_index(self.app, self.config, self.systems)
                self.emit("database-index", index)
                self.emit("validation", "Database update complete")
            except Exception as error:
                self.emit("validation", "Failed")
                self.emit("log", str(error))
            finally:
                self.emit("done")
        threading.Thread(target=worker, daemon=True).start()

    def emit(self, *event):
        self.events.put(event)

    def font(self, size, bold=False, mono=False):
        key = (int(size), bool(bold), bool(mono))
        if key not in self._font_cache:
            if mono:
                name = "DejaVuSansMono-Bold.ttf" if bold else "DejaVuSansMono.ttf"
            else:
                name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
            path = Path("/usr/share/fonts/truetype/dejavu") / name
            self._font_cache[key] = ImageFont.truetype(str(path), int(size)) if path.exists() else ImageFont.load_default()
        return self._font_cache[key]
    def _fit_text(self, draw, text, font, width, suffix="..."):
        value = str(text or "")
        if draw.textbbox((0, 0), value, font=font)[2] <= width:
            return value
        room = max(0, width - draw.textbbox((0, 0), suffix, font=font)[2])
        while value and draw.textbbox((0, 0), value, font=font)[2] > room:
            value = value[:-1]
        return value.rstrip() + suffix
    def _wrap_pixels(self, draw, text, font, width, lines=3):
        words = str(text or "").split()
        output, current = [], ""
        for word in words:
            candidate = (current + " " + word).strip()
            if current and draw.textbbox((0, 0), candidate, font=font)[2] > width:
                output.append(current)
                current = word
                if len(output) >= lines:
                    break
            else:
                current = candidate
        if current and len(output) < lines:
            output.append(self._fit_text(draw, current, font, width))
        return output
    def _heading(self, image, draw, title, icon=None, y=74):
        x = LAYOUT_LEFT
        if icon and self._paste_icon(image, icon, (x, y - 2, x + 30, y + 28)):
            x += 38
        font = self.font(20, True)
        draw.text((x, y), self._fit_text(draw, title, font, LAYOUT_RIGHT - x), font=font, fill=(195, 210, 230))
    def _load_icons(self):
        path = self.app / "assets" / "retroscrape-icons.png"
        try:
            sheet = Image.open(str(path)).convert("RGBA")
            self.icon_sheet = sheet
            cols, rows = 6, 3
            if sheet.width % cols or sheet.height % rows:
                raise ValueError("Icon sheet geometry is invalid")
            cell_w, cell_h = sheet.width // cols, sheet.height // rows
            names = ("scan", "bios", "scrape", "artwork", "database", "update", "mame", "report", "folder", "cache", "settings", "success", "warning", "error", "storage", "back", "visibility", "refresh")
            for index, name in enumerate(names):
                col, row = index % cols, index // cols
                cell = sheet.crop((col * cell_w, row * cell_h, (col + 1) * cell_w, (row + 1) * cell_h))
                alpha_box = cell.getchannel("A").getbbox()
                if alpha_box is None:
                    raise ValueError("Icon cell is empty: %s" % name)
                artwork = cell.crop(alpha_box)
                artwork.thumbnail((72, 72), Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS)
                normalised = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
                normalised.alpha_composite(artwork, ((96 - artwork.width) // 2, (96 - artwork.height) // 2))
                self.icon_cells[name] = normalised
        except (OSError, ValueError):
            self.icon_sheet = None
            self.icon_cells = {}
    def _paste_icon(self, image, name, box):
        icon = self.icon_cells.get(name)
        if icon is None:
            return False
        width, height = box[2] - box[0], box[3] - box[1]
        resized = icon.resize((width, height), Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS)
        image.paste(resized, (box[0], box[1]), resized)
        return True
    def _system_badge(self, key, size=34):
        cache_key = (str(key), int(size))
        cached = self.system_badges.get(cache_key)
        if cached is not None:
            return cached
        kind = SYSTEM_ICON_TYPES.get(str(key), "generic")
        colour = SYSTEM_ICON_COLOURS.get(str(key), (85, 110, 145))
        badge = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(badge)
        scale = size / 34.0
        def pt(value): return int(round(value * scale))
        def box(x1, y1, x2, y2): return (pt(x1), pt(y1), pt(x2), pt(y2))
        light = (242, 247, 252, 255)
        detail = (25, 40, 58, 255)
        accent = colour + (255,)
        draw.rounded_rectangle(box(1, 1, 32, 32), radius=pt(6), fill=accent, outline=(185, 215, 238, 255), width=max(1, pt(1)))
        def rect(coords, radius=1, fill=light, outline=None, width=1):
            draw.rounded_rectangle(box(*coords), radius=pt(radius), fill=fill, outline=outline, width=max(1, pt(width)))
        def line(coords, fill=detail, width=2): draw.line(tuple(pt(v) for v in coords), fill=fill, width=max(1, pt(width)))
        def ellipse(coords, fill=light, outline=None, width=1): draw.ellipse(box(*coords), fill=fill, outline=outline, width=max(1, pt(width)))
        if kind == "keyboard":
            rect((6, 10, 28, 23), 2); rect((8, 13, 26, 19), 1, detail); line((8, 25, 26, 25), light, 2)
            for x in (10, 14, 18, 22): line((x, 20, x, 22), detail, 1)
        elif kind == "cube":
            rect((8, 9, 25, 26), 3); line((11, 9, 11, 6), light, 2); line((11, 6, 23, 6), light, 2); ellipse((13, 13, 20, 20), detail)
        elif kind == "frontload":
            rect((5, 9, 29, 25), 2); rect((9, 12, 25, 18), 1, detail); line((9, 21, 20, 21), detail, 2); ellipse((24, 20, 27, 23), detail)
        elif kind in ("cart", "curved_cart", "oval_cart", "flat_cart"):
            if kind == "oval_cart": ellipse((4, 10, 30, 26), light)
            elif kind == "curved_cart": rect((4, 11, 30, 26), 6)
            elif kind == "flat_cart": rect((4, 12, 30, 25), 1)
            else: rect((5, 10, 29, 26), 4)
            rect((11, 7, 23, 15), 1, detail); line((9, 21, 25, 21), detail, 2); ellipse((7, 22, 10, 25), detail); ellipse((24, 22, 27, 25), detail)
        elif kind == "tower":
            rect((12, 5, 23, 29), 3); line((15, 9, 20, 9), detail, 1); ellipse((16, 24, 19, 27), detail)
        elif kind == "tablet":
            rect((4, 9, 30, 25), 4); rect((10, 12, 24, 22), 1, detail); line((7, 16, 7, 20), detail, 2); line((5, 18, 9, 18), detail, 2); ellipse((26, 16, 28, 18), detail); ellipse((24, 20, 26, 22), detail)
        elif kind == "arcadestick":
            rect((5, 18, 29, 27), 3); line((12, 18, 12, 10), light, 2); ellipse((9, 7, 15, 13), light); ellipse((20, 20, 24, 24), detail); ellipse((25, 19, 28, 22), detail)
        elif kind in ("disc", "disc_ports", "disc_ports2"):
            rect((5, 8, 29, 27), 3); ellipse((11, 10, 23, 22), detail); ellipse((15, 14, 19, 18), light)
            count = 4 if kind == "disc_ports" else (2 if kind == "disc_ports2" else 0)
            for i in range(count): rect((7 + i * 5, 23, 10 + i * 5, 25), 1, detail)
        elif kind == "cabinet":
            draw.polygon([(pt(9),pt(5)),(pt(25),pt(5)),(pt(27),pt(29)),(pt(7),pt(29))], fill=light)
            rect((11, 9, 23, 18), 1, detail); line((12, 22, 12, 25), detail, 1); ellipse((10, 20, 14, 23), detail); ellipse((18, 21, 21, 24), detail); ellipse((23, 20, 26, 23), detail)
        elif kind in ("handheld_v", "handheld_round"):
            rect((9, 4, 25, 30), 5 if kind == "handheld_round" else 3); rect((12, 7, 22, 17), 1, detail); line((13, 22, 13, 27), detail, 2); line((10, 24, 16, 24), detail, 2); ellipse((19, 22, 22, 25), detail); ellipse((22, 25, 24, 27), detail)
        elif kind in ("handheld_h", "wide_handheld", "slim_handheld"):
            y1,y2=(10,25) if kind != "slim_handheld" else (12,23)
            rect((3, y1, 31, y2), 5); rect((11, y1+2, 23, y2-2), 1, detail); line((7, y1+4, 7, y2-3), detail, 2); line((4, (y1+y2)//2, 10, (y1+y2)//2), detail, 2); ellipse((26, y1+4, 29, y1+7), detail); ellipse((24, y2-6, 27, y2-3), detail)
        elif kind == "dual":
            rect((7, 3, 27, 16), 3); rect((7, 18, 27, 31), 3); rect((10, 6, 24, 13), 1, detail); rect((10, 21, 24, 27), 1, detail); line((9, 17, 25, 17), light, 1)
        elif kind == "stacked":
            rect((6, 8, 27, 14), 1); rect((8, 15, 29, 21), 1); rect((5, 22, 26, 27), 1); line((10, 11, 22, 11), detail, 1); ellipse((24, 17, 26, 19), detail)
        else:
            rect((5, 10, 29, 26), 4); rect((11, 7, 23, 14), 1, detail); line((9, 21, 25, 21), detail, 2)
        self.system_badges[cache_key] = badge
        return badge
    def _paste_system_badge(self, image, key, box):
        width, height = box[2] - box[0], box[3] - box[1]
        badge = self._system_badge(key, min(width, height))
        x = box[0] + (width - badge.width) // 2
        y = box[1] + (height - badge.height) // 2
        image.paste(badge, (x, y), badge)
        return True

    def save_config(self):
        try:
            durable_json(self.app / "config" / "config.json", self.config)
            return True
        except OSError as error:
            self.logs = (self.logs + ["Configuration not saved: %s" % error])[-8:]
            self.show_notice("Setting changed for this session only", "warning")
            return False

    def cycle_system(self, direction):
        if not self.system_keys:
            return
        selected = self.config.get("selected_system")
        current = self.system_keys.index(selected) if selected in self.system_keys else 0
        self.config["selected_system"] = self.system_keys[(current + direction) % len(self.system_keys)]
        self.save_config()
        self.dirty = True

    def system_root(self, profile):
        if profile.get("provider") == "xmame":
            path = Path(self.config["xmame"]["rom_dir"])
            if path.is_dir(): return path
            raise RuntimeError("MAME folder not found: %s" % path)
        validator = DatValidator(self.app, self.config, profile, self.config["selected_system"], self.emit, self.stop)
        return validator.detect_root()

    def start(self, bios=False, rebuild=False):
        if self.running:
            return
        self.running = True
        self.operation = "mame_rebuild" if rebuild else ("bios_scan" if bios else "game_scan")
        self.page = "run"
        while True:
            try:
                self.events.get_nowait()
            except queue.Empty:
                break
        self.stop.clear()
        self.pending_report = None
        self.report_data = None
        self.report_path = None
        self.pending_bios_validator = None
        self.prompt_yes = True
        self.issue_items = []
        self.issue_scroll = 0
        self.organisation = None
        self.organisation_items = []
        self.organisation_scroll = 0
        self.organisation_result = None
        self.logs = []
        self.summary = {}
        self.progress = (0, 0, "")
        self.stage = "Starting"
        self.validation = "Running"
        self.scraper_status = "Disabled" if not self.config["thegamesdb"].get("enabled") else "Waiting"
        self.scraper_exit_code = None
        self.dirty = True

        def worker():
            scrape_thread = None
            scraper = None
            failed = False
            cancelled = False
            try:
                system = self.config["selected_system"]
                profile = self.systems[system]
                if rebuild:
                    XMameValidator(self.app, self.config, self.emit, self.stop).ensure_xml(force=True)
                    self.emit("validation", "Database rebuilt")
                elif bios:
                    validator = BiosValidator(self.app, self.config, self.emit, self.stop)
                    if validator.system_dat() is None:
                        self.emit("stage", "Downloading BIOS database")
                        DatabaseUpdater(self.app, self.config, self.emit, self.stop).run()
                        index = build_index(self.app, self.config, self.systems)
                        self.emit("database-index", index)
                    payload = validator.scan()
                    self.emit("bios-validator", validator)
                    self.emit("result", payload)
                    self.emit("summary", payload["summary"])
                    self.emit("validation", "Complete")
                else:
                    rom_root = self.system_root(profile)
                    if not has_processable_content(rom_root):
                        self.emit("empty-system", system, str(rom_root))
                        return
                    if self.config["thegamesdb"].get("enabled"):
                        scraper = TheGamesDBScraper(self.app, self.config, profile, system, rom_root, self.emit, self.stop)
                    if profile.get("provider") == "xmame":
                        payload = XMameValidator(self.app, self.config, self.emit, self.stop).scan_games()
                        self.emit("result", payload)
                        self.emit("summary", payload["summary"])
                        self.emit("validation", "Complete")
                    else:
                        validator = DatValidator(self.app, self.config, profile, system, self.emit, self.stop)
                        if validator.validation_supported():
                            payload = validator.scan()
                            self.emit("result", payload)
                            self.emit("summary", payload["summary"])
                            self.emit("validation", "Complete")
                        else:
                            self.emit("validation", "Not supported")
                            self.emit("log", "No matching DAT: scraping only")
                    if profile.get("provider") == "xmame" and not self.stop.is_set():
                        try:
                            self.emit("stage", "Checking folder organisation")
                            organisation = ArcadeOrganiser(self.app, self.config, self.emit, self.stop).scan(rom_root)
                            self.emit("organisation-result", organisation)
                        except Cancelled:
                            raise
                        except Exception as error:
                            self.emit("scraper-log", "Organisation scan unavailable: %s" % error)
                    if scraper and not self.stop.is_set():
                        try:
                            self.emit("scraper-result", scraper.run())
                        except Cancelled:
                            raise
                        except ProviderPaused as paused:
                            self.emit("scraper-status", "Paused")
                            self.emit("scraper-log", paused.result.get("message", "Scraping paused"))
                            self.emit("scraper-result", paused.result)
                        except Exception as error:
                            self.emit("scraper-status", "Failed")
                            self.emit("scraper-log", "TheGamesDB error: %s" % error)
                            self.emit("scraper-result", {"provider": "TheGamesDB", "status": "Failed", "error": str(error)})
            except Cancelled:
                cancelled = True
                self.emit("validation", "Cancelled")
            except Exception as error:
                failed = True
                self.emit("validation", "Failed: %s" % error)
                self.emit("log", traceback.format_exc())
            finally:
                self.emit("done")

        threading.Thread(target=worker, daemon=True).start()

    def start_organisation_apply(self):
        if self.running or not self.organisation:
            return
        self.running = True
        self.operation = "organisation"
        self.page = "run"
        self.stop.clear()
        self.logs = []
        self.progress = (0, 0, "")
        self.stage = "Applying organisation suggestions"
        self.validation = "Moving confirmed ROMs"
        self.scraper_status = "Not used"
        self.dirty = True
        def worker():
            try:
                result = ArcadeOrganiser(self.app, self.config, self.emit, self.stop).apply(self.organisation)
                self.emit("organisation-applied", result)
                self.emit("validation", "Organisation complete")
            except Cancelled:
                self.emit("validation", "Cancelled")
            except Exception as error:
                self.emit("validation", "Organisation failed: %s" % error)
                self.emit("log", traceback.format_exc())
            finally:
                self.emit("done")
        threading.Thread(target=worker, daemon=True).start()

    def _load_report_at(self, index):
        if not self.report_paths:
            self.report_data = None
            self.report_path = None
            self.validation = "No saved report"
            self.scraper_status = "Not recorded"
            self.summary = {}
            self.issue_items = []
            self.logs = []
            return
        self.report_index = max(0, min(int(index), len(self.report_paths) - 1))
        path = self.report_paths[self.report_index]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("report root is not an object")
            self.report_data = data
            self.report_path = path
            validation = data.get("validation") if isinstance(data.get("validation"), dict) else None
            if validation:
                self.validation = str(validation.get("status", "Not recorded"))
            elif data.get("type") == "scrape":
                self.validation = "Not run"
            elif data.get("type") in ("dat", "mame", "bios") and data.get("results") is not None:
                self.validation = "Complete"
            else:
                self.validation = "Not recorded"
            scraping = data.get("scraping", {}) if isinstance(data.get("scraping"), dict) else {}
            self.scraper_status = str(scraping.get("status", "Not recorded"))
            self.summary = data.get("summary", {}) if isinstance(data.get("summary"), dict) else {}
            if data.get("type") == "scrape" and not self.summary:
                self.summary = {
                    "MATCHED": int(scraping.get("matched", 0) or 0),
                    "NOT_FOUND": int(scraping.get("not_found", 0) or 0),
                    "LOOKUP_FAILED": int(scraping.get("lookup_failures", 0) or 0),
                    "ARTWORK_PENDING": len(scraping.get("media_pending_details", {}) or {}),
                }
            self.issue_items = issue_lines(data)
            organisation = data.get("organisation", {}) if isinstance(data.get("organisation"), dict) else {}
            self.organisation_items = [item for item in organisation.get("results", []) if item.get("status") == "MOVE_RECOMMENDED"]
            self.logs = [path.name]
        except Exception as error:
            self.report_data = None
            self.report_path = path
            self.validation = "Report error"
            self.logs = [str(error)]

    def show_last_report(self):
        try:
            self.report_paths = sorted((self.app / "data").glob("report-*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        except OSError:
            self.report_paths = []
        self.page = "report"
        self.report_index = 0
        self.issue_scroll = 0
        self.organisation_items = []
        self._load_report_at(0)
        self.dirty = True

    def activate(self):
        if self.page == "advanced":
            item = self.advanced[self.selection]
            if item == "Show unavailable systems":
                self.config["show_missing_systems"] = not self.config.get("show_missing_systems", False)
                saved = self.save_config()
                self.refresh_system_keys()
                if saved:
                    self.show_notice("Unavailable systems are now shown" if self.config["show_missing_systems"] else "Unavailable systems are now hidden", "success")
            elif item == "Refresh Systems":
                before = set(self.system_keys)
                self.rebuild_database_index()
                after = set(self.system_keys)
                if before == after:
                    self.show_notice("Systems checked: no changes", "info")
                else:
                    self.show_notice("Systems refreshed: %d available" % len(after), "success")
            elif item == "Database Status":
                self.show_database_status()
            elif item == "Remove Database Backup":
                self.page = "cleanup_confirm"
                self.prompt_yes = False
            else:
                self.menu_selections["advanced"] = self.selection
                self.page = "settings"
                self.selection = self.menu_selections["settings"]
            self.dirty = True
            return True
        items = self.settings if self.page == "settings" else self.home
        item = items[self.selection]
        if self.page == "settings":
            if item == "Game scraping":
                self.config["thegamesdb"]["enabled"] = not self.config["thegamesdb"].get("enabled", False)
                saved = self.save_config()
                self.rebuild_database_index()
                if saved:
                    self.show_notice("Game scraping enabled" if self.config["thegamesdb"]["enabled"] else "Game scraping disabled", "success")
            elif item == "TheGamesDB Usage":
                self.menu_selections["settings"] = self.selection
                self.start_api_status(refresh=True)
            elif item == "Update Databases":
                self.start_database_update()
            elif item == "Rebuild MAME Database":
                self.start(rebuild=True)
            elif item == "Advanced":
                self.menu_selections["settings"] = self.selection
                self.page = "advanced"
                self.selection = self.menu_selections["advanced"]
            else:
                self.page = "home"
                self.selection = 0
        elif item == "Scan Games":
            selected = self.config.get("selected_system")
            self.system_selection = self.system_keys.index(selected) if selected in self.system_keys else 0
            self.system_scroll = max(0, min(self.system_selection, max(0, len(self.system_keys) - self.system_rows)))
            self.page = "systems"
        elif item == "Scan BIOS":
            self.start(bios=True)
        elif item == "Last Report":
            self.menu_selections["home"] = self.selection
            self.show_last_report()
        else:
            self.menu_selections["home"] = self.selection
            self.page = "settings"
            self.selection = self.menu_selections["settings"]
        self.dirty = True
        return True

    def save_pending_report(self):
        if self.pending_report is None:
            return None
        path = save_report(self.app, self.pending_report)
        self.logs = (self.logs + ["Saved: " + path.name])[-8:]
        self.show_notice("Report saved", "success")
        self.pending_report = None
        return path

    def _page_move(self, direction):
        """Move one visible page. L1 always moves back; R1 always moves forward."""
        if direction not in (-1, 1) or self.running:
            return False
        if self.page in ("settings", "advanced"):
            items = self.advanced if self.page == "advanced" else self.settings
            if not items:
                return False
            self.selection = max(0, min(len(items) - 1, self.selection + direction * self.menu_rows))
            self.menu_scroll = max(0, min(self.selection, max(0, len(items) - self.menu_rows)))
            self.menu_selections[self.page] = self.selection
        elif self.page == "systems":
            if not self.system_keys:
                return False
            relative = self.system_selection - self.system_scroll
            target_scroll = max(0, min(self.system_scroll + direction * self.system_rows, max(0, len(self.system_keys) - self.system_rows)))
            self.system_scroll = target_scroll
            self.system_selection = min(len(self.system_keys) - 1, target_scroll + max(0, min(relative, self.system_rows - 1)))
        elif self.page == "details":
            maximum = max(0, len(self.detail_rows) - self.detail_rows_visible)
            self.detail_scroll = max(0, min(maximum, self.detail_scroll + direction * self.detail_rows_visible))
        elif self.page == "issues":
            visual_count = sum(len(self._wrap_text(str(value).replace("\n", " "), 88, 3)) for value in self.issue_items)
            maximum = max(0, visual_count - self.issue_rows)
            self.issue_scroll = max(0, min(maximum, self.issue_scroll + direction * self.issue_rows))
        elif self.page == "suggestions":
            maximum = max(0, len(self.organisation_items) - self.organisation_rows)
            self.organisation_scroll = max(0, min(maximum, self.organisation_scroll + direction * self.organisation_rows))
        elif self.page == "report":
            if not self.report_paths:
                return False
            self._load_report_at(self.report_index - direction)
        else:
            return False
        self.dirty = True
        return True

    def button(self, button):
        if button == MENU:
            return self.button(B)
        if button == MENU_HOLD:
            return True
        if button in (L1, R1):
            self._page_move(-1 if button == L1 else 1)
            return True
        if self.page == "startup":
            return True
        if self.page == "startup_error":
            return False if button == B else True
        if self.page == "bios_fix_confirm":
            if button in (LEFT, RIGHT):
                self.prompt_yes = not self.prompt_yes
                self.dirty = True
            elif button in (A, B):
                if button == A and self.prompt_yes and self.pending_report is not None and self.pending_bios_validator is not None:
                    try:
                        changed = self.pending_bios_validator.apply_fixes(self.pending_report)
                        self.summary = self.pending_report.get("summary", {})
                        self.issue_items = issue_lines(self.pending_report)
                        self.logs = (self.logs + ["BIOS files fixed: %d" % changed])[-8:]
                        self.show_notice("BIOS files fixed: %d" % changed, "success" if changed else "info")
                    except (OSError, RuntimeError) as error:
                        self.logs = (self.logs + ["BIOS fix failed: %s" % error])[-8:]
                        self.show_notice("BIOS fixes could not be applied", "error")
                else:
                    self.logs = (self.logs + ["BIOS files left unchanged"])[-8:]
                    self.show_notice("BIOS files left unchanged", "neutral")
                duplicates = any(item.get("status") == "DUPLICATE" for item in self.pending_report.get("results", []))
                self.page = "bios_duplicate_confirm" if duplicates else "save_report"
                if not duplicates:
                    self.pending_bios_validator = None
                self.prompt_yes = True
                self.dirty = True
            return True
        if self.page == "bios_duplicate_confirm":
            if button in (LEFT, RIGHT):
                self.prompt_yes = not self.prompt_yes
                self.dirty = True
            elif button in (A, B):
                if button == A and self.prompt_yes and self.pending_report is not None and self.pending_bios_validator is not None:
                    try:
                        deleted = self.pending_bios_validator.delete_duplicates(self.pending_report)
                        self.summary = self.pending_report.get("summary", {})
                        self.issue_items = issue_lines(self.pending_report)
                        self.logs = (self.logs + ["BIOS duplicates deleted: %d" % deleted])[-8:]
                        self.show_notice("BIOS duplicates removed: %d" % deleted, "success" if deleted else "info")
                    except (OSError, RuntimeError) as error:
                        self.logs = (self.logs + ["Duplicate deletion failed: %s" % error])[-8:]
                        self.show_notice("BIOS duplicates could not be removed", "error")
                else:
                    self.logs = (self.logs + ["BIOS duplicates kept"])[-8:]
                    self.show_notice("BIOS duplicates kept", "neutral")
                self.pending_bios_validator = None
                self.page = "save_report"
                self.prompt_yes = True
                self.dirty = True
            return True
        if self.page == "save_report":
            if button in (LEFT, RIGHT):
                self.prompt_yes = not self.prompt_yes
                self.dirty = True
            elif button == A:
                if self.prompt_yes:
                    try:
                        self.save_pending_report()
                    except OSError as error:
                        self.logs = (self.logs + ["Report not saved: %s" % error])[-8:]
                        self.show_notice("Report could not be saved", "error")
                        self.pending_report = None
                else:
                    self.pending_report = None
                    self.logs = (self.logs + ["Report not saved"]) [-8:]
                    self.show_notice("Report not saved", "neutral")
                self.page = "results"
                self.dirty = True
            elif button == B:
                self.pending_report = None
                self.show_notice("Report not saved", "neutral")
                self.page = "results"
                self.dirty = True
            return True
        if self.page == "cleanup_confirm":
            if button in (LEFT, RIGHT):
                self.prompt_yes = not self.prompt_yes
                self.dirty = True
            elif button == A:
                if self.prompt_yes:
                    try:
                        updater = DatabaseUpdater(self.app, self.config, self.emit, self.stop)
                        existed = updater.previous.exists()
                        updater.cleanup_previous()
                        message = "Database backup removed" if existed else "No database backup to remove"
                        self.logs = (self.logs + [message])[-8:]
                        self.show_notice(message, "success" if existed else "info")
                    except Exception as error:
                        self.logs = (self.logs + ["Cleanup failed: %s" % error])[-8:]
                        self.show_notice("Backup could not be removed", "error")
                self.page = "advanced"
                self.dirty = True
            elif button == B:
                self.page = "advanced"
                self.dirty = True
            return True
        if self.page == "organisation_confirm":
            if button in (LEFT, RIGHT):
                self.prompt_yes = not self.prompt_yes
                self.dirty = True
            elif button == A:
                if self.prompt_yes:
                    self.start_organisation_apply()
                else:
                    self.page = "results"
                    self.dirty = True
            elif button == B:
                self.page = "results"
                self.dirty = True
            return True
        if self.page == "suggestions":
            if button == A and self.organisation_items:
                self.page = "organisation_confirm"
                self.prompt_yes = False
                self.dirty = True
            elif button == B:
                self.page = "results"
                self.dirty = True
            return True
        if self.page == "results":
            if button == A and self.issue_items:
                self.page = "issues"
                self.issue_scroll = 0
                self.dirty = True
            elif button == X and self.organisation_items:
                self.page = "suggestions"
                self.organisation_scroll = 0
                self.dirty = True
            elif button == B:
                self.pending_report = None
                self.page = "home"
                self.selection = self.menu_selections["home"]
                self.dirty = True
            return True
        if self.page == "issues":
            if button == B:
                self.page = "report" if self.report_data is not None and self.pending_report is None else "results"
                self.dirty = True
            return True
        if self.page == "details":
            if button == B:
                self.page = self.detail_back
                self.dirty = True
            return True
        if self.page == "report":
            if button == LEFT and self.report_index < len(self.report_paths) - 1:
                self._load_report_at(self.report_index + 1)
                self.dirty = True
            elif button == RIGHT and self.report_index > 0:
                self._load_report_at(self.report_index - 1)
                self.dirty = True
            elif button == A and self.issue_items:
                self.page = "issues"
                self.issue_scroll = 0
                self.dirty = True
            elif button == X and self.report_data is not None:
                self.show_report_details()
            elif button == B:
                self.page = "home"
                self.selection = self.menu_selections["home"]
                self.dirty = True
            return True
        if self.page == "run":
            if button == Y and self.running:
                self.stop.set()
                self.logs.append("Cancellation requested")
                self.dirty = True
            elif button == B and not self.running:
                self.page = "home"
                self.selection = 0
                self.dirty = True
            return True
        if self.page == "system_details":
            if button == A and self.system_keys:
                self.config["selected_system"] = self.system_keys[self.system_selection]
                self.save_config()
                self.start()
            elif button == B:
                self.page = "systems"
                self.dirty = True
            return True
        if self.page == "systems":
            if button == A and self.system_keys:
                self.config["selected_system"] = self.system_keys[self.system_selection]
                self.save_config()
                self.start()
            elif button == X and self.system_keys:
                self.page = "system_details"
                self.dirty = True
            elif button == B:
                self.page = "home"
                self.selection = self.menu_selections["home"]
                self.dirty = True
            return True
        if button == X and self.page == "settings" and self.settings[self.selection] in ("Game scraping", "TheGamesDB Usage"):
            self.start_api_status(refresh=True)
            return True
        if self.page == "api_status":
            if button == Y:
                scraper = TheGamesDBScraper(self.app, self.config, {}, self.config.get("selected_system", ""), self.app, self.emit, self.stop)
                removed = scraper.cleanup_expired_cache()
                self.api_status = scraper.cached_allowance_status()
                self.api_status_error = ""
                self.api_status_message = ("Expired cache removed: %d" % removed) if removed else "No expired cache entries"
                self.dirty = True
            elif button == X:
                self.start_api_status(refresh=True)
            elif button == B:
                self.page = "settings"
                self.selection = self.menu_selections["settings"]
                self.dirty = True
            return True
        if button == A:
            return self.activate()
        if button == B:
            if self.page in ("settings", "advanced"):
                if self.page == "advanced":
                    self.menu_selections["advanced"] = self.selection
                    self.page = "settings"
                    self.selection = self.menu_selections["settings"]
                else:
                    self.menu_selections["settings"] = self.selection
                    self.page = "home"
                    self.selection = self.menu_selections["home"]
                self.dirty = True
                return True
            return False
        if button == X and self.page == "home":
            selected = self.config.get("selected_system")
            self.system_selection = self.system_keys.index(selected) if selected in self.system_keys else 0
            self.system_scroll = max(0, min(self.system_selection, max(0, len(self.system_keys) - self.system_rows)))
            self.page = "systems"
            self.dirty = True
        return True

    def navigate(self, value):
        if self.page == "startup":
            return
        if self.page == "details":
            maximum = max(0, len(self.detail_rows) - self.detail_rows_visible)
            if value & UP:
                self.detail_scroll = max(0, self.detail_scroll - 1)
            elif value & DOWN:
                self.detail_scroll = min(maximum, self.detail_scroll + 1)
            self.dirty = True
            return
        if self.page == "suggestions":
            maximum = max(0, len(self.organisation_items) - self.organisation_rows)
            if value & UP:
                self.organisation_scroll = max(0, self.organisation_scroll - 1)
            elif value & DOWN:
                self.organisation_scroll = min(maximum, self.organisation_scroll + 1)
            self.dirty = True
            return
        if self.page == "issues":
            visual_count = sum(len(self._wrap_text(str(value).replace("\n", " "), 88, 3)) for value in self.issue_items)
            maximum = max(0, visual_count - self.issue_rows)
            if value & UP:
                self.issue_scroll = max(0, self.issue_scroll - 1)
            elif value & DOWN:
                self.issue_scroll = min(maximum, self.issue_scroll + 1)
            self.dirty = True
            return
        if self.page == "organisation_confirm":
            if value & (LEFT | RIGHT):
                self.prompt_yes = not self.prompt_yes
                self.dirty = True
            return
        if self.page == "cleanup_confirm":
            if value & (LEFT | RIGHT):
                self.prompt_yes = not self.prompt_yes
                self.dirty = True
            return
        if self.page in ("bios_fix_confirm", "bios_duplicate_confirm", "save_report"):
            if value & (LEFT | RIGHT):
                self.prompt_yes = not self.prompt_yes
                self.dirty = True
            return
        if self.page == "systems":
            if not self.system_keys:
                return
            if value & UP:
                self.system_selection = max(0, self.system_selection - 1)
            elif value & DOWN:
                self.system_selection = min(len(self.system_keys) - 1, self.system_selection + 1)
            if self.system_selection < self.system_scroll:
                self.system_scroll = self.system_selection
            elif self.system_selection >= self.system_scroll + self.system_rows:
                self.system_scroll = self.system_selection - self.system_rows + 1
            self.dirty = True
            return
        if self.page not in ("home", "settings", "advanced"):
            return
        items = self.advanced if self.page == "advanced" else (self.settings if self.page == "settings" else self.home)
        if not items:
            return
        if value & UP:
            self.selection = max(0, self.selection - 1)
        elif value & DOWN:
            self.selection = min(len(items) - 1, self.selection + 1)
        self.menu_selections[self.page] = self.selection
        if self.page in ("settings", "advanced"):
            if self.selection < self.menu_scroll:
                self.menu_scroll = self.selection
            elif self.selection >= self.menu_scroll + self.menu_rows:
                self.menu_scroll = self.selection - self.menu_rows + 1
            if self.selection == 0:
                self.menu_scroll = 0
        else:
            self.menu_scroll = 0
        self.dirty = True

    def drain(self):
        changed = False
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            changed = True
            kind = event[0]
            if kind == "progress":
                self.progress = event[1:]
            elif kind == "activity":
                self.stage = str(event[1])
            elif kind == "stage":
                self.stage = str(event[1])
            elif kind == "bios-validator":
                self.pending_bios_validator = event[1]
            elif kind == "result":
                self.pending_report = event[1]
                self.pending_report.setdefault("validation", {"status": "Complete", "provider": "xMAME" if event[1].get("type") == "mame" else ("System.dat" if event[1].get("type") == "bios" else "DAT")})
                if event[1].get("type") != "bios":
                    self.pending_report.setdefault("scraping", {"enabled": bool(self.config["thegamesdb"].get("enabled")), "status": self.scraper_status, "exit_code": self.scraper_exit_code})
                self.issue_items = issue_lines(event[1])
            elif kind == "summary":
                self.summary = event[1]
            elif kind == "database-index":
                self.database_index = event[1]
                self.refresh_system_keys()
                self.system_selection = min(self.system_selection, max(0, len(self.system_keys) - 1))
                self.system_scroll = min(self.system_scroll, max(0, len(self.system_keys) - self.system_rows))
            elif kind == "startup-stage":
                self.stage = str(event[1])
                self.progress = (0, 0, self.stage)
            elif kind == "startup-complete":
                self.database_index = event[1]
                self.refresh_system_keys()
                if event[2]:
                    self.logs = (self.logs + [str(event[2])])[-8:]
                self.stage = "Ready"
                self.page = "home"
            elif kind == "startup-failed":
                self.validation = "Startup failed: %s" % event[1]
                if event[2]:
                    self.logs = (self.logs + [str(event[2])])[-8:]
                self.logs = (self.logs + [self.validation, "Press B to exit and check logs/boot.log"])[-8:]
                self.page = "startup_error"
            elif kind == "empty-system":
                self.validation = "No games found"
                self.logs = (self.logs + ["No processable game files in " + str(event[2])])[-8:]
                self.pending_report = None
                self.rebuild_database_index()
                self.page = "systems"
            elif kind == "validation":
                self.validation = str(event[1])
            elif kind == "api-status":
                self.api_status = event[1]
                self.api_status_error = ""
                self.api_status_message = "Allowance refreshed"
                self.api_status_loading = False
            elif kind == "api-status-error":
                self.api_status_error = str(event[1])
                self.api_status_message = ""
                self.api_status_loading = False
            elif kind == "scraper-status":
                self.scraper_status = str(event[1])
            elif kind == "scraper-storage":
                storage = event[1] if isinstance(event[1], dict) else {}
                self.logs = (self.logs + ["Artwork estimate: %s for %d games; %s free" % (self._format_bytes(storage.get("estimated_bytes", 0)), int(storage.get("pending_artwork", 0) or 0), self._format_bytes(storage.get("free_bytes", 0)))])[-8:]
            elif kind == "organisation-result":
                self.organisation = event[1]
                self.organisation_items = [item for item in event[1].get("results", []) if item.get("status") == "MOVE_RECOMMENDED"]
                self.organisation_scroll = 0
                if self.pending_report is not None:
                    self.pending_report["organisation"] = event[1]
                if self.organisation_items:
                    self.logs = (self.logs + ["Folder suggestions: %d" % len(self.organisation_items)])[-8:]
            elif kind == "organisation-applied":
                self.organisation_result = event[1]
                moved = int(event[1].get("moved", 0))
                self.logs = (self.logs + ["ROMs moved: %d" % moved])[-8:]
                self.show_notice("ROMs moved: %d" % moved if moved else "No ROMs needed moving", "success" if moved else "info")
                self.organisation_items = []
                self.database_index = build_index(self.app, self.config, self.systems)
                self.refresh_system_keys()
                if self.pending_report is not None:
                    self.pending_report["organisation_applied"] = event[1]
            elif kind == "scraper-result":
                result = dict(event[1])
                self.logs = (self.logs + ["TheGamesDB: " + str(result.get("status", "Complete"))])[-8:]
                if self.pending_report is None:
                    self.pending_report = {"report_version": 5, "type": "scrape", "system": self.config.get("selected_system"), "summary": {}, "results": [], "validation": {"status": "Not run", "provider": "None"}, "scraping": result}
                else:
                    self.pending_report["scraping"] = result
                self.issue_items = issue_lines(self.pending_report)
            elif kind == "scraper-log":
                self.logs = (self.logs + [str(event[1])])[-8:]
            elif kind in ("log", "report"):
                self.logs = (self.logs + [str(event[1])])[-8:]
            elif kind == "done":
                self.running = False
                if self.operation == "database_update":
                    self.show_notice("DAT databases updated" if self.validation == "Database update complete" else "Database update failed", "success" if self.validation == "Database update complete" else "error")
                elif self.operation == "mame_rebuild":
                    self.show_notice("MAME database rebuilt" if self.validation == "Database rebuilt" else "MAME database rebuild failed", "success" if self.validation == "Database rebuilt" else "error")
                if self.organisation_result is not None:
                    self.page = "results"
                    self.organisation_result = None
                elif self.pending_report is not None:
                    actionable = any(item.get("status") in ("BADPATH", "RENAME", "BADPATH_RENAME") for item in self.pending_report.get("results", []))
                    duplicates = any(item.get("status") == "DUPLICATE" for item in self.pending_report.get("results", []))
                    if self.pending_report.get("type") == "bios" and actionable:
                        self.page = "bios_fix_confirm"
                    elif self.pending_report.get("type") == "bios" and duplicates:
                        self.page = "bios_duplicate_confirm"
                    else:
                        self.page = "save_report"
                        self.pending_bios_validator = None
                    self.prompt_yes = True
                else:
                    self.logs.append("Finished. Press B to return.")
        if changed:
            self.dirty = True

    def draw(self):
        image = Image.new("RGB", (W, H), (9, 15, 25))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, W, 58), fill=(24, 82, 145))
        draw.text((18, 12), "RetroScrape", font=self.font(28, True), fill="white")
        header_font = self.font(14, True)
        header_label = self._fit_text(draw, self._header_context(), header_font, 210)
        header_width = draw.textbbox((0, 0), header_label, font=header_font)[2]
        draw.text((620 - header_width, 18), header_label, font=header_font, fill=(190, 225, 255))
        if self.page in ("home", "settings", "advanced"):
            items = self.advanced if self.page == "advanced" else (self.settings if self.page == "settings" else self.home)
            heading = "Advanced" if self.page == "advanced" else ("Settings" if self.page == "settings" else "Choose an action")
            draw.text((24, 74), heading, font=self.font(18, True), fill=(195, 210, 230))
            y = 126
            scrollable = self.page in ("settings", "advanced")
            start = self.menu_scroll if scrollable else 0
            visible_items = items[start:start + self.menu_rows] if scrollable else items
            for offset, item in enumerate(visible_items):
                index = start + offset
                active = index == self.selection
                draw.rounded_rectangle((MENU_ROW_LEFT, y, MENU_ROW_RIGHT, y + MENU_ROW_HEIGHT), radius=12, fill=(35, 95, 155) if active else (23, 34, 52), outline=(95, 150, 205) if active else (45, 65, 90), width=2)
                icon_name = MENU_ICONS.get(item)
                text_x = 58
                icon_y = y + (MENU_ROW_HEIGHT - MENU_ICON_SIZE) // 2
                if icon_name and self._paste_icon(image, icon_name, (MENU_ICON_X, icon_y, MENU_ICON_X + MENU_ICON_SIZE, icon_y + MENU_ICON_SIZE)):
                    text_x = MENU_TEXT_X
                item_font = self.font(18, active)
                fitted_item = self._fit_text(draw, item, item_font, 430)
                text_box = draw.textbbox((0, 0), fitted_item, font=item_font)
                text_y = y + (MENU_ROW_HEIGHT - (text_box[3] - text_box[1])) // 2 - text_box[1]
                draw.text((text_x, text_y), fitted_item, font=item_font, fill="white")
                value = ""
                if self.page == "settings" and item == "Game scraping":
                    value = "On" if self.config["thegamesdb"].get("enabled") else "Off"
                elif self.page == "advanced" and item == "Show unavailable systems":
                    value = "On" if self.config.get("show_missing_systems") else "Off"
                if value:
                    value_width = draw.textbbox((0, 0), value, font=self.font(16, True))[2]
                    value_font = self.font(16, True); value_box = draw.textbbox((0, 0), value, font=value_font); value_y = y + (MENU_ROW_HEIGHT - (value_box[3] - value_box[1])) // 2 - value_box[1]; draw.text((584 - value_width, value_y), value, font=value_font, fill=(125, 235, 165))
                y += 66
            paging = "   [L1/R1] Page" if scrollable and len(items) > self.menu_rows else ""
            if self.page == "settings" and self.settings[self.selection] == "Game scraping":
                footer = "[A] Toggle" + paging + "   [B] Back"
            elif self.page == "settings" and self.settings[self.selection] == "TheGamesDB Usage":
                footer = "[A] Open" + paging + "   [B] Back"
            else:
                footer = ("[Up/Down] Choose   [A] Select   [B] Exit" if self.page == "home" else "[Up/Down] Choose" + paging + "   [A] Select   [B] Back")
            if scrollable and self.menu_scroll > 0:
                draw.text((612, 104), "^", font=self.font(12, True), fill=(165, 185, 210))
            if scrollable and self.menu_scroll + self.menu_rows < len(items):
                draw.text((612, 432), "v", font=self.font(12, True), fill=(165, 185, 210))
            self._draw_notice(draw, 431)
            draw.text((24, 454), footer, font=self.font(13), fill=(165, 185, 210))
        elif self.page == "api_status":
            self._heading(image, draw, "TheGamesDB Usage", "cache")
            status = self.api_status or {}
            draw.rounded_rectangle((32, 102, 608, 220), radius=10, fill=(18, 29, 45), outline=(45, 65, 90), width=1)
            draw.text((48, 112), "ALLOWANCE", font=self.font(11, True), fill=(130, 190, 255))
            rows = [
                ("Available", status.get("total_available_allowance", "Unknown")),
                ("Monthly", status.get("remaining_monthly_allowance", "Unknown")),
                ("Extra", status.get("extra_allowance", 0)),
                ("Resets in", self._duration(status.get("allowance_refresh_timer"))),
            ]
            y = 134
            for label_text, value in rows:
                draw.text((48, y), label_text, font=self.font(12), fill=(205, 215, 230))
                value_text = str(value); width = draw.textbbox((0, 0), value_text, font=self.font(12, True))[2]
                draw.text((592 - width, y), value_text, font=self.font(12, True, True), fill=(125, 235, 165)); y += 22
            draw.rounded_rectangle((32, 230, 608, 326), radius=10, fill=(18, 29, 45), outline=(45, 65, 90), width=1)
            draw.text((48, 240), "LOCAL ACTIVITY", font=self.font(11, True), fill=(130, 190, 255))
            calls = int(status.get("period_network_requests", 0) or 0)
            hits = int(status.get("period_cache_hits", 0) or 0)
            ratio = ("%.0f%%" % (hits * 100.0 / (calls + hits))) if calls + hits else "0%"
            activity = [("Calls this period", calls), ("Cache hits", hits), ("Cache hit ratio", ratio)]
            y = 263
            for label_text, value in activity:
                draw.text((48, y), label_text, font=self.font(12), fill=(205, 215, 230)); value_text = str(value)
                width = draw.textbbox((0, 0), value_text, font=self.font(12, True))[2]; draw.text((592 - width, y), value_text, font=self.font(12, True, True), fill="white"); y += 24
            draw.rounded_rectangle((32, 336, 608, 418), radius=10, fill=(18, 29, 45), outline=(45, 65, 90), width=1)
            draw.text((48, 346), "CACHE", font=self.font(11, True), fill=(130, 190, 255))
            cache_rows = [("Entries", status.get("cache_entries", 0)), ("Expired", status.get("cache_expired", 0)), ("Size", self._format_bytes(status.get("cache_bytes", 0)))]
            y = 364
            for label_text, value in cache_rows:
                draw.text((48, y), label_text, font=self.font(12), fill=(205, 215, 230)); value_text = str(value)
                width = draw.textbbox((0, 0), value_text, font=self.font(12, True))[2]; draw.text((592 - width, y), value_text, font=self.font(12, True, True), fill="white"); y += 20
            checked = "Refreshing..." if self.api_status_loading else ("Checked " + self._clock(status.get("checked_at")))
            note = self.api_status_error or self.api_status_message or checked
            colour = (255, 125, 115) if self.api_status_error else ((125, 235, 165) if self.api_status_message else (190, 205, 225))
            note_font = self.font(10); draw.text((38, 426), self._fit_text(draw, note, note_font, 564), font=note_font, fill=colour)
            draw.text((24, 454), "[X] Refresh   [Y] Remove expired   [B] Back", font=self.font(12), fill=(165, 185, 210))
        elif self.page == "system_details":
            key = self.system_keys[self.system_selection] if self.system_keys else ""
            entry = self.database_index.get("systems", {}).get(key, {})
            name = self.systems.get(key, {}).get("name", key)
            self._paste_system_badge(image, key, (24, 70, 58, 104))
            title_font = self.font(20, True)
            draw.text((66, 76), self._fit_text(draw, name, title_font, 500), font=title_font, fill=(195, 210, 230))
            rows = [
                ("Available action", entry.get("action", entry.get("capability", "Unavailable"))),
                ("ROM folder", entry.get("rom_folder", "") or "Not found"),
                ("Validator", entry.get("validator", "") or "None"),
                ("Validation", entry.get("validation_status", "unavailable").replace("-", " ").title()),
                ("Database", entry.get("database_status", "not applicable").replace("-", " ").title()),
                ("Selected DAT", Path(entry.get("selected_dat", "")).name if entry.get("selected_dat") else "None"),
            ]
            y = 106
            for label_text, value in rows:
                draw.text((34, y), label_text, font=self.font(13), fill=(190, 205, 225))
                text = str(value)
                value_font = self.font(13, True); draw.text((190, y), self._fit_text(draw, text, value_font, 404), font=value_font, fill="white")
                y += 31
            detail = "ROM: %s | DAT: %s | %s" % (entry.get("rom_folder", "") or "Not found", entry.get("selected_dat", "") or "None", entry.get("database_detail") or entry.get("validation_detail", "") or "No validation issue recorded")
            draw.line((34, 292, 604, 292), fill=(45, 65, 90), width=1)
            draw.text((34, 304), "Details", font=self.font(13, True), fill=(255, 190, 110))
            lines = self._wrap_text(detail, 74, 5)
            detail_y = 328
            for line in lines:
                draw.text((34, detail_y), line, font=self.font(12), fill=(220, 225, 235))
                detail_y += 20
            errors = entry.get("errors", [])
            if len(errors) > 1:
                draw.text((34, 398), "%d rejected DAT candidates" % len(errors), font=self.font(12), fill=(255, 190, 110))
            draw.text((24, 454), "A start   [B] Back", font=self.font(13), fill=(165, 185, 210))
        elif self.page == "systems":
            draw.text((24, 74), "Choose a system", font=self.font(18, True), fill=(195, 210, 230))
            visible = self.system_keys[self.system_scroll:self.system_scroll + self.system_rows]
            if not visible:
                draw.text((44, 145), "No systems with processable game files", font=self.font(16, True), fill=(255, 190, 110))
                draw.text((44, 180), "Enable Show unavailable systems in Advanced.", font=self.font(13), fill=(190, 205, 225))
            y = 102
            for offset, key in enumerate(visible):
                index = self.system_scroll + offset
                active = index == self.system_selection
                label = self.systems[key].get("name", key)
                provider = self.database_index.get("systems", {}).get(key, {}).get("capability", "Unknown")
                draw.rounded_rectangle((36, y, 604, y + 46), radius=10, fill=(35, 95, 155) if active else (23, 34, 52), outline=(95, 150, 205) if active else (45, 65, 90), width=2)
                provider_font = self.font(11, True)
                provider_width = draw.textbbox((0, 0), provider, font=provider_font)[2]
                self._paste_system_badge(image, key, (46, y + 6, 80, y + 40))
                label_font = self.font(16, active)
                label_text = self._fit_text(draw, label, label_font, max(80, 474 - provider_width))
                label_box = draw.textbbox((0, 0), label_text, font=label_font)
                label_y = y + (46 - (label_box[3] - label_box[1])) // 2 - label_box[1]
                draw.text((90, label_y), label_text, font=label_font, fill="white")
                capability_colours = {"Validate + scrape": (125, 235, 165), "Validate only": (130, 190, 255), "Scrape only": (255, 190, 110), "Unavailable": (145, 155, 170)}
                draw.text((584 - provider_width, y + 15), provider, font=provider_font, fill=capability_colours.get(provider, (190, 205, 225)))
                y += 51
            if self.system_scroll > 0:
                draw.text((610, 91), "^", font=self.font(12, True), fill=(165, 185, 210))
            if self.system_scroll + self.system_rows < len(self.system_keys):
                draw.text((610, 412), "v", font=self.font(12, True), fill=(165, 185, 210))
            draw.text((24, 454), "[Up/Down] Choose   [L1/R1] Page   [A] Start   [X] Details   [B] Back", font=self.font(13), fill=(165, 185, 210))
        elif self.page == "report":
            data = self.report_data or {}
            system_name = self.systems.get(data.get("system"), {}).get("name", data.get("system") or data.get("type", "No saved report"))
            self._heading(image, draw, "Report History", "report")
            if self.report_paths:
                position = "%d of %d" % (self.report_index + 1, len(self.report_paths))
                width = draw.textbbox((0, 0), position, font=self.font(12, True))[2]
                draw.text((600 - width, 82), position, font=self.font(12, True), fill=(190, 205, 225))
            if not self.report_paths:
                draw.text((44, 150), "No saved reports", font=self.font(18, True), fill="white")
                draw.text((44, 188), "Saved scan reports will appear here.", font=self.font(13), fill=(190, 205, 225))
                draw.text((24, 454), "[B] Back", font=self.font(12), fill=(165, 185, 210))
                return image
            draw.text((34, 112), "System", font=self.font(13), fill=(190, 205, 225))
            draw.text((180, 112), str(system_name)[:48], font=self.font(13, True), fill="white")
            draw.text((34, 142), "Saved", font=self.font(13), fill=(190, 205, 225))
            draw.text((180, 142), str(data.get("saved_at", "Unknown"))[:48], font=self.font(13, True), fill="white")
            draw.text((34, 172), "Validation", font=self.font(13), fill=(190, 205, 225))
            draw.text((180, 172), self.validation[:48], font=self.font(13, True), fill="white")
            draw.text((34, 202), "Scraping", font=self.font(13), fill=(190, 205, 225))
            draw.text((180, 202), self.scraper_status[:48], font=self.font(13, True), fill="white")
            summary_items = self._summary_items()
            y = 246
            for status, count in summary_items[:5]:
                draw.text((54, y), self._status_label(status), font=self.font(13, True), fill=self._status_colour(status))
                count_font = self.font(13, True, True); count_text = str(count); count_width = draw.textbbox((0, 0), count_text, font=count_font)[2]; draw.text((584 - count_width, y), count_text, font=count_font, fill="white")
                y += 28
            if len(summary_items) > 5:
                draw.text((54, y), "+%d more in Issues" % (len(summary_items) - 5), font=self.font(11, True), fill=(190, 205, 225))
            parts = []
            if self.issue_items: parts.append("[A] Issues")
            if self.report_data is not None: parts.append("[X] Details")
            if len(self.report_paths) > 1: parts.append("[Left/Right/L1/R1] History")
            parts.append("[B] Back")
            draw.text((24, 454), "   ".join(parts), font=self.font(13), fill=(165, 185, 210))
        elif self.page == "details":
            self._heading(image, draw, self.detail_title, "database" if self.detail_title == "Database Status" else "report")
            visible_details = self.detail_rows[self.detail_scroll:self.detail_scroll + self.detail_rows_visible]
            y = 108
            for label_text, value in visible_details:
                draw.text((34, y), str(label_text), font=self.font(13), fill=(190, 205, 225))
                text = str(value)
                value_font = self.font(13, True)
                fitted = self._fit_text(draw, text, value_font, 380)
                width = draw.textbbox((0, 0), fitted, font=value_font)[2]
                draw.text((600 - width, y), fitted, font=value_font, fill="white")
                y += 32
            if self.detail_scroll > 0:
                draw.text((610, 92), "^", font=self.font(12, True), fill=(165, 185, 210))
            if self.detail_scroll + self.detail_rows_visible < len(self.detail_rows):
                draw.text((610, 377), "v", font=self.font(12, True), fill=(165, 185, 210))
            draw.line((34, 370, 604, 370), fill=(45, 65, 90), width=1)
            if self.detail_text:
                detail_y = 380
                for line in self._wrap_text(self.detail_text, 82, 3):
                    draw.text((34, detail_y), line, font=self.font(10), fill=(255, 190, 110))
                    detail_y += 14
            footer = "[Up/Down] Scroll   [L1/R1] Page   [B] Back" if len(self.detail_rows) > self.detail_rows_visible else "[B] Back"
            draw.text((24, 454), footer, font=self.font(12), fill=(165, 185, 210))
        elif self.page == "cleanup_confirm":
            draw.text((24, 82), "Remove database backup?", font=self.font(20, True), fill=(195, 210, 230))
            draw.text((24, 124), "This removes the retained rollback copy.", font=self.font(14), fill=(255, 190, 110))
            y = 190
            for index, label in enumerate(("Yes", "No")):
                active = (index == 0 and self.prompt_yes) or (index == 1 and not self.prompt_yes)
                draw.rounded_rectangle((120, y, 520, y + 60), radius=12, fill=(35, 95, 155) if active else (23, 34, 52), outline=(95, 150, 205) if active else (45, 65, 90), width=2)
                label_font = self.font(18, active); label_width = draw.textbbox((0, 0), label, font=label_font)[2]; draw.text((320 - label_width // 2, y + 18), label, font=label_font, fill="white")
                y += 78
            draw.text((24, 454), "[Left/Right] Choose   [A] Confirm   [B] Back", font=self.font(13), fill=(165, 185, 210))
        elif self.page == "results":
            system_key = self.config.get("selected_system", "")
            if self.operation == "game_scan" and system_key:
                self._paste_system_badge(image, system_key, (24, 70, 54, 100))
                results_font = self.font(20, True)
                draw.text((62, 76), "Scan Results", font=results_font, fill=(195, 210, 230))
            else:
                self._heading(image, draw, "Scan Results", "success" if not self.issue_items else "warning")
            draw.rounded_rectangle((32, 106, 304, 158), radius=9, fill=(23, 34, 52), outline=(45, 65, 90), width=1)
            validation_colour = self._status_colour(self.validation)
            validation_icon = "error" if validation_colour == (255, 125, 115) else ("success" if validation_colour == (125, 235, 165) else "warning")
            self._paste_icon(image, validation_icon, (44, 114, 80, 150))
            draw.text((88, 114), "VALIDATION", font=self.font(10, True), fill=(130, 190, 255))
            validation_font = self.font(13, True)
            draw.text((88, 132), self._fit_text(draw, self._status_label(self.validation), validation_font, 200), font=validation_font, fill=validation_colour)
            draw.rounded_rectangle((336, 106, 608, 158), radius=9, fill=(23, 34, 52), outline=(45, 65, 90), width=1)
            scrape_colour = self._status_colour(self.scraper_status)
            scrape_icon = "error" if scrape_colour == (255, 125, 115) else ("success" if scrape_colour == (125, 235, 165) else "artwork")
            self._paste_icon(image, scrape_icon, (348, 114, 384, 150))
            draw.text((392, 114), "SCRAPING", font=self.font(10, True), fill=(130, 190, 255))
            scrape_font = self.font(13, True)
            draw.text((392, 132), self._fit_text(draw, self._status_label(self.scraper_status), scrape_font, 200), font=scrape_font, fill=scrape_colour)
            draw.line((32, 174, 608, 174), fill=(45, 65, 90), width=1)
            summary_items = self._summary_items()
            if not summary_items:
                draw.text((48, 214), "No summary available", font=self.font(16, True), fill="white")
                draw.text((48, 246), "The operation did not produce result counts.", font=self.font(12), fill=(190, 205, 225))
            y = 192
            for status, count in summary_items[:6]:
                colour = self._status_colour(status)
                draw.text((56, y), self._status_label(status), font=self.font(14, True), fill=colour)
                count_font = self.font(14, True, True); count_text = str(count); count_width = draw.textbbox((0, 0), count_text, font=count_font)[2]; draw.text((584 - count_width, y), count_text, font=count_font, fill=colour)
                y += 29
            if len(summary_items) > 6:
                draw.text((56, y), "+%d more in Issues" % (len(summary_items) - 6), font=self.font(11, True), fill=(190, 205, 225))
            parts = []
            if self.issue_items:
                parts.append("[A] Issues")
            if self.organisation_items:
                parts.append("[X] Suggestions")
            parts.append("[B] Done")
            self._draw_notice(draw, 431)
            draw.text((24, 454), "   ".join(parts), font=self.font(13), fill=(165, 185, 210))
        elif self.page == "suggestions":
            self._heading(image, draw, "Folder suggestions", "folder")
            draw.text((24, 98), "Review only. Nothing moves until confirmed.", font=self.font(12), fill=(255, 190, 110))
            y = 126
            for item in self.organisation_items[self.organisation_scroll:self.organisation_scroll + self.organisation_rows]:
                source = Path(item.get("path", "")).parent.name
                target = Path(item.get("target", "")).parent.name
                name_font = self.font(14, True)
                route_font = self.font(12, True)
                reason_font = self.font(11)
                route = "%s -> %s" % (source, target)
                draw.text((28, y), self._fit_text(draw, item.get("name", ""), name_font, 310), font=name_font, fill="white")
                route_text = self._fit_text(draw, route, route_font, 244)
                route_width = draw.textbbox((0, 0), route_text, font=route_font)[2]
                draw.text((604 - route_width, y + 1), route_text, font=route_font, fill=(125, 235, 165))
                draw.text((28, y + 20), self._fit_text(draw, item.get("reason", ""), reason_font, 576), font=reason_font, fill=(190, 205, 225))
                y += 45
            draw.text((24, 454), "[Up/Down] Scroll   [L1/R1] Page   [A] Apply   [B] Back", font=self.font(13), fill=(165, 185, 210))
        elif self.page == "organisation_confirm":
            count = len(self.organisation_items)
            draw.text((24, 82), "Apply folder suggestions?", font=self.font(20, True), fill=(195, 210, 230))
            draw.text((24, 124), "Move %d ROMs and update exact playlist paths." % count, font=self.font(14), fill="white")
            draw.text((24, 154), "Backups and rollback protection will be used.", font=self.font(13), fill=(255, 190, 110))
            y = 202
            for index, label in enumerate(("Yes", "No")):
                active = (index == 0 and self.prompt_yes) or (index == 1 and not self.prompt_yes)
                draw.rounded_rectangle((120, y, 520, y + 60), radius=12, fill=(35, 95, 155) if active else (23, 34, 52), outline=(95, 150, 205) if active else (45, 65, 90), width=2)
                label_font = self.font(18, active); label_width = draw.textbbox((0, 0), label, font=label_font)[2]; draw.text((320 - label_width // 2, y + 18), label, font=label_font, fill="white")
                y += 78
            draw.text((24, 454), "[Left/Right] Choose   [A] Confirm   [B] Back", font=self.font(13), fill=(165, 185, 210))
        elif self.page == "issues":
            self._heading(image, draw, "Scan issues", "warning")
            visual_issues = []
            for value in self.issue_items:
                visual_issues.extend(self._wrap_text(str(value).replace("\n", " "), 88, 3))
            y = 108
            for line in visual_issues[self.issue_scroll:self.issue_scroll + self.issue_rows]:
                draw.text((28, y), line, font=self.font(12), fill=(235, 220, 190))
                y += 39
            if self.issue_scroll > 0:
                draw.text((610, 92), "^", font=self.font(12, True), fill=(165, 185, 210))
            if self.issue_scroll + self.issue_rows < len(visual_issues):
                draw.text((610, 422), "v", font=self.font(12, True), fill=(165, 185, 210))
            draw.text((24, 454), "[Up/Down] Scroll   [L1/R1] Page   [B] Back", font=self.font(13), fill=(165, 185, 210))
        elif self.page == "bios_fix_confirm":
            draw.text((24, 82), "BIOS fixes found", font=self.font(20, True), fill=(195, 210, 230)); draw.text((24, 128), "Fix BIOS names and paths?", font=self.font(18, True), fill="white"); draw.text((24, 158), "Only verified matches will be changed.", font=self.font(13), fill=(255, 190, 110)); y = 202
            for index, label in enumerate(("Yes", "No")):
                active = (index == 0 and self.prompt_yes) or (index == 1 and not self.prompt_yes); draw.rounded_rectangle((120, y, 520, y + 60), radius=12, fill=(35, 95, 155) if active else (23, 34, 52), outline=(95, 150, 205) if active else (45, 65, 90), width=2); label_font = self.font(18, active); label_width = draw.textbbox((0, 0), label, font=label_font)[2]; draw.text((320 - label_width // 2, y + 18), label, font=label_font, fill="white"); y += 78
            draw.text((24, 454), "[Left/Right] Choose   [A] Confirm   [B] Leave unchanged", font=self.font(13), fill=(165, 185, 210))
        elif self.page == "bios_duplicate_confirm":
            draw.text((24, 82), "Duplicate BIOS found", font=self.font(20, True), fill=(195, 210, 230))
            draw.text((24, 128), "Delete verified duplicates?", font=self.font(18, True), fill="white")
            draw.text((24, 158), "Canonical files will be kept.", font=self.font(13), fill=(255, 190, 110))
            y = 202
            for index, label in enumerate(("Yes", "No")):
                active = (index == 0 and self.prompt_yes) or (index == 1 and not self.prompt_yes)
                draw.rounded_rectangle((120, y, 520, y + 60), radius=12, fill=(35, 95, 155) if active else (23, 34, 52), outline=(95, 150, 205) if active else (45, 65, 90), width=2)
                label_font = self.font(18, active); label_width = draw.textbbox((0, 0), label, font=label_font)[2]; draw.text((320 - label_width // 2, y + 18), label, font=label_font, fill="white")
                y += 78
            draw.text((24, 454), "[Left/Right] Choose   [A] Confirm   [B] Keep duplicates", font=self.font(13), fill=(165, 185, 210))
        elif self.page == "save_report":
            draw.text((24, 82), "Scan complete", font=self.font(20, True), fill=(195, 210, 230))
            draw.text((24, 128), "Save this report?", font=self.font(18, True), fill="white")
            y = 190
            for index, label in enumerate(("Yes", "No")):
                active = (index == 0 and self.prompt_yes) or (index == 1 and not self.prompt_yes)
                draw.rounded_rectangle((120, y, 520, y + 60), radius=12, fill=(35, 95, 155) if active else (23, 34, 52), outline=(95, 150, 205) if active else (45, 65, 90), width=2)
                label_font = self.font(18, active); label_width = draw.textbbox((0, 0), label, font=label_font)[2]; draw.text((320 - label_width // 2, y + 18), label, font=label_font, fill="white")
                y += 78
            draw.text((24, 454), "[Left/Right] Choose   [A] Confirm   [B] Do not save", font=self.font(13), fill=(165, 185, 210))
        else:
            run_title_x = 24
            if self.operation == "game_scan":
                system_key = self.config.get("selected_system", "")
                self._paste_system_badge(image, system_key, (24, 66, 54, 96))
                run_title_x = 62
            stage_font = self.font(16, True)
            draw.text((run_title_x, 72), self._fit_text(draw, self.stage, stage_font, 616 - run_title_x), font=stage_font, fill=(130, 215, 255))
            validation_text = "Unavailable" if self.validation == "Not supported" else self.validation
            scraping_text = "Unavailable" if self.scraper_status in ("Disabled", "Not used") else self.scraper_status
            if self.operation == "database_update":
                draw.text((24, 96), "Database: %s" % validation_text, font=self.font(14), fill=(205, 215, 230))
            elif self.operation == "mame_rebuild":
                draw.text((24, 96), "MAME database: %s" % validation_text, font=self.font(14), fill=(205, 215, 230))
            elif self.operation == "organisation":
                draw.text((24, 96), "Organisation: %s" % validation_text, font=self.font(14), fill=(205, 215, 230))
            elif self.operation == "bios_scan":
                draw.text((24, 96), "BIOS validation: %s" % validation_text, font=self.font(14), fill=(205, 215, 230))
            else:
                draw.rounded_rectangle((24, 92, 312, 116), radius=6, fill=(18, 29, 45), outline=(45, 65, 90), width=1)
                draw.rounded_rectangle((328, 92, 616, 116), radius=6, fill=(18, 29, 45), outline=(45, 65, 90), width=1)
                validation_font = self.font(12, True)
                scraping_font = self.font(12, True)
                draw.text((34, 97), self._fit_text(draw, "Validation: %s" % validation_text, validation_font, 268), font=validation_font, fill=self._status_colour(validation_text))
                draw.text((338, 97), self._fit_text(draw, "Scraping: %s" % scraping_text, scraping_font, 268), font=scraping_font, fill=self._status_colour(scraping_text))
            current, total, label = self.progress
            draw.rectangle((24, 126, 616, 150), fill=(25, 35, 50))
            if total:
                percent = max(0, min(100, int(current * 100 / total)))
                draw.rectangle((24, 126, 24 + 592 * percent // 100, 150), fill=(35, 155, 105))
                progress_text = "%d%% %s" % (percent, label)
            elif current:
                phase = (current // (256 * 1024)) % 24
                x = 24 + int((592 - 120) * phase / 23)
                draw.rectangle((x, 126, x + 120, 150), fill=(35, 155, 105))
                progress_text = "%.1f MiB %s" % (current / (1024.0 * 1024.0), label)
            else:
                phase = int(time.monotonic() * 8) % 24
                x = 24 + int((592 - 120) * phase / 23)
                draw.rectangle((x, 126, x + 120, 150), fill=(35, 155, 105))
                progress_text = label
                self.dirty = True
            progress_font = self.font(13, True)
            progress_text = self._fit_text(draw, progress_text, progress_font, 560)
            progress_width = draw.textbbox((0, 0), progress_text, font=progress_font)[2]
            draw.text((24 + max(0, (592 - progress_width) // 2), 127), progress_text, font=progress_font, fill="white")
            visual_logs = []
            for value in self.logs:
                visual_logs.extend(self._wrap_text(str(value).replace("\n", " "), 82, 3))
            y = 170
            for line in visual_logs[-8:]:
                draw.text((28, y), line, font=self.font(13), fill=(220, 225, 235))
                y += 25
            if self.summary:
                compact = "  ".join("%s: %s" % (self._status_label(key), value) for key, value in self._summary_items()[:4])
                draw.text((24, 405), compact[:78], font=self.font(13, True), fill=(190, 205, 225))
            if not self.running:
                self._draw_notice(draw, 431)
            draw.text((24, 454), "[Y] Cancel" if self.running else "[B] Return", font=self.font(14), fill=(255, 190, 110))
        return image

    def run(self):
        library = ctypes.util.find_library("SDL2") or "libSDL2-2.0.so.0"
        sdl = ctypes.CDLL(library)
        bind_sdl(sdl)
        if sdl.SDL_Init(INIT_VIDEO | INIT_JOY) != 0:
            raise RuntimeError("SDL_Init failed: %s" % sdl_error(sdl))
        joystick = None
        window = None
        renderer = None
        texture = None
        screen = Path("/tmp/retroscrape-screen.bmp")
        try:
            if sdl.SDL_NumJoysticks() > 0:
                joystick = sdl.SDL_JoystickOpen(0)
                raw_name = sdl.SDL_JoystickNameForIndex(0)
                self.joystick_name = raw_name.decode("utf-8", "replace") if raw_name else "Unknown"
                if self.joystick_name != "ANBERNIC-keys":
                    self.startup_warning = ((self.startup_warning + " ") if self.startup_warning else "") + "SDL controller: %s" % self.joystick_name
            window = sdl.SDL_CreateWindow(b"RetroScrape", POS, POS, W, H, FULL)
            if not window:
                raise RuntimeError("SDL_CreateWindow failed: %s" % sdl_error(sdl))
            renderer = sdl.SDL_CreateRenderer(window, -1, ACC | VSYNC)
            if not renderer:
                renderer = sdl.SDL_CreateRenderer(window, -1, SOFT)
            if not renderer:
                raise RuntimeError("SDL_CreateRenderer failed: %s" % sdl_error(sdl))
            event = ctypes.create_string_buffer(64)
            self.start_initialisation()
            alive = True
            while alive:
                self.drain()
                if self.page == "startup":
                    self.dirty = True
                if self.notice_text:
                    if time.monotonic() >= self.notice_until:
                        self.notice_text = ""
                    self.dirty = True
                if self.dirty:
                    self.draw().save(str(screen), "BMP")
                    if texture:
                        sdl.SDL_DestroyTexture(texture)
                    rwops = sdl.SDL_RWFromFile(str(screen).encode(), b"rb")
                    if not rwops:
                        raise RuntimeError("SDL_RWFromFile failed: %s" % sdl_error(sdl))
                    surface = sdl.SDL_LoadBMP_RW(rwops, 1)
                    if not surface:
                        raise RuntimeError("SDL_LoadBMP_RW failed: %s" % sdl_error(sdl))
                    texture = sdl.SDL_CreateTextureFromSurface(renderer, surface)
                    sdl.SDL_FreeSurface(surface)
                    if not texture:
                        raise RuntimeError("SDL_CreateTextureFromSurface failed: %s" % sdl_error(sdl))
                    sdl.SDL_RenderClear(renderer)
                    sdl.SDL_RenderCopy(renderer, texture, None, None)
                    sdl.SDL_RenderPresent(renderer)
                    self.dirty = False
                while sdl.SDL_PollEvent(ctypes.byref(event)):
                    raw = event.raw
                    kind = int.from_bytes(raw[:4], sys.byteorder)
                    if kind == QUIT:
                        alive = False
                    elif kind == BDOWN:
                        alive = self.button(raw[12])
                    elif kind == HAT:
                        self.navigate(raw[13])
                sdl.SDL_Delay(20)
        finally:
            self.stop.set()
            if texture:
                sdl.SDL_DestroyTexture(texture)
            if renderer:
                sdl.SDL_DestroyRenderer(renderer)
            if window:
                sdl.SDL_DestroyWindow(window)
            if joystick:
                sdl.SDL_JoystickClose(joystick)
            screen.unlink(missing_ok=True)
            sdl.SDL_Quit()
        return 0


def sdl_error(sdl):
    value = sdl.SDL_GetError()
    return value.decode("utf-8", "replace") if value else "unknown SDL error"


def bind_sdl(sdl):
    sdl.SDL_GetError.restype = ctypes.c_char_p
    sdl.SDL_Init.argtypes = [ctypes.c_uint32]
    sdl.SDL_Init.restype = ctypes.c_int
    sdl.SDL_Quit.argtypes = []
    sdl.SDL_CreateWindow.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint32]
    sdl.SDL_CreateWindow.restype = ctypes.c_void_p
    sdl.SDL_DestroyWindow.argtypes = [ctypes.c_void_p]
    sdl.SDL_CreateRenderer.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32]
    sdl.SDL_CreateRenderer.restype = ctypes.c_void_p
    sdl.SDL_DestroyRenderer.argtypes = [ctypes.c_void_p]
    sdl.SDL_RWFromFile.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    sdl.SDL_RWFromFile.restype = ctypes.c_void_p
    sdl.SDL_LoadBMP_RW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    sdl.SDL_LoadBMP_RW.restype = ctypes.c_void_p
    sdl.SDL_FreeSurface.argtypes = [ctypes.c_void_p]
    sdl.SDL_CreateTextureFromSurface.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    sdl.SDL_CreateTextureFromSurface.restype = ctypes.c_void_p
    sdl.SDL_DestroyTexture.argtypes = [ctypes.c_void_p]
    sdl.SDL_RenderClear.argtypes = [ctypes.c_void_p]
    sdl.SDL_RenderCopy.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    sdl.SDL_RenderPresent.argtypes = [ctypes.c_void_p]
    sdl.SDL_PollEvent.argtypes = [ctypes.c_void_p]
    sdl.SDL_PollEvent.restype = ctypes.c_int
    sdl.SDL_Delay.argtypes = [ctypes.c_uint32]
    sdl.SDL_NumJoysticks.restype = ctypes.c_int
    sdl.SDL_JoystickNameForIndex.argtypes = [ctypes.c_int]
    sdl.SDL_JoystickNameForIndex.restype = ctypes.c_char_p
    sdl.SDL_JoystickOpen.argtypes = [ctypes.c_int]
    sdl.SDL_JoystickOpen.restype = ctypes.c_void_p
    sdl.SDL_JoystickClose.argtypes = [ctypes.c_void_p]
