import binascii
import hashlib
import re
import time
import zipfile
from collections import Counter
from pathlib import Path

from .storage import durable_json, mark_persistent_write
from .xmame import Cancelled
from .database_index import dat_candidates, detect_rom_folder, inspect_dat, iter_processable_files, specialised_dat_reason

IGNORED = {".png", ".jpg", ".jpeg", ".webp", ".xml", ".json", ".txt", ".log", ".cfg", ".ini", ".sav", ".srm", ".state", ".mp4", ".pdf"}


class DatValidator:
    def __init__(self, app_dir, config, profile, system, emit, stop_event):
        self.app_dir = Path(app_dir)
        self.config = config
        self.profile = profile
        self.system = system
        self.emit = emit
        self.stop_event = stop_event
        self.entries = []
        self.by_crc = {}
        self.by_md5 = {}
        self.by_sha1 = {}

    def check_cancelled(self):
        if self.stop_event.is_set():
            raise Cancelled()

    def detect_root(self):
        root = detect_rom_folder(self.config, self.profile)
        if root is not None:
            return root
        raise RuntimeError("No ROM folder detected for %s" % self.profile.get("name", self.system))
    def dat_files(self):
        local, downloaded = dat_candidates(self.app_dir, self.profile, self.system)
        valid_local = [path for path in local if inspect_dat(path)[0]]
        valid_downloaded = [path for path in downloaded if inspect_dat(path)[0] and not specialised_dat_reason(path)]
        if valid_local:
            return [valid_local[0]]
        if valid_downloaded:
            return [valid_downloaded[0]]
        return []

    def validation_supported(self):
        return bool(self.dat_files())

    @staticmethod
    def _blocks(text):
        position = 0
        while True:
            match = re.search(r"\brom\s*\(", text[position:], re.IGNORECASE)
            if not match:
                return
            start = position + match.end()
            depth, quoted, escaped, index = 1, False, False, start
            while index < len(text) and depth:
                char = text[index]
                if escaped:
                    escaped = False
                elif char == "\\" and quoted:
                    escaped = True
                elif char == '"':
                    quoted = not quoted
                elif not quoted:
                    if char == "(": depth += 1
                    elif char == ")": depth -= 1
                index += 1
            if depth:
                return
            yield text[start:index - 1], index
            position = index

    def build_index(self):
        files = self.dat_files()
        attribute = re.compile(r'\b(name|size|crc|md5|sha1)\s+("(?:\\.|[^"])*"|[^\s\)]+)', re.IGNORECASE)
        for number, path in enumerate(files, 1):
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                chunks = iter(lambda: handle.read(256 * 1024), "")
                buffer = ""
                for chunk in chunks:
                    self.check_cancelled()
                    buffer += chunk
                    position = 0
                    parsed = list(self._blocks(buffer))
                    blocks = [block for block, _end in parsed]
                    if parsed:
                        buffer = buffer[parsed[-1][1]:]
                    elif len(buffer) > 2 * 1024 * 1024:
                        buffer = buffer[-1024 * 1024:]
                    for block in blocks:
                        attrs = {}
                        for key, value in attribute.findall(block):
                            attrs[key.lower()] = value[1:-1] if value.startswith('"') else value
                        if not attrs.get("name") or not any(attrs.get(key) for key in ("crc", "md5", "sha1")):
                            continue
                        entry = {"name": attrs["name"], "size": int(attrs["size"]) if attrs.get("size", "").isdigit() else None, "crc": attrs.get("crc", "").lower().zfill(8), "md5": attrs.get("md5", "").lower(), "sha1": attrs.get("sha1", "").lower(), "dat": path.name}
                        self.entries.append(entry)
                        for key, table in (("crc", self.by_crc), ("md5", self.by_md5), ("sha1", self.by_sha1)):
                            if entry[key]: table.setdefault(entry[key], []).append(entry)
            self.emit("progress", number, len(files), "DAT files")
            self.check_cancelled()
            continue

    def _hash(self, stream):
        crc, md5, sha1, size = 0, hashlib.md5(), hashlib.sha1(), 0
        while True:
            block = stream.read(1024 * 1024)
            if not block: break
            size += len(block); crc = binascii.crc32(block, crc); md5.update(block); sha1.update(block); self.check_cancelled()
        return size, "%08x" % (crc & 0xffffffff), md5.hexdigest(), sha1.hexdigest()

    def _matches(self, size, crc, md5, sha1):
        found, seen = [], set()
        for value, table in ((sha1, self.by_sha1), (md5, self.by_md5), (crc, self.by_crc)):
            if not value:
                continue
            for entry in table.get(value, []):
                identity = id(entry)
                if identity in seen:
                    continue
                seen.add(identity)
                if entry["size"] is not None and entry["size"] != size:
                    continue
                if entry["crc"] and entry["crc"] != crc:
                    continue
                if entry["md5"] and entry["md5"] != md5:
                    continue
                if entry["sha1"] and entry["sha1"] != sha1:
                    continue
                found.append(entry)
        return found
    def _match(self, name, size, crc, md5, sha1):
        candidates = self._matches(size, crc, md5, sha1)
        if not candidates:
            return None
        actual = str(name).replace("\\", "/")
        actual_base = Path(actual).name
        candidates.sort(key=lambda entry: (
            entry["name"] != actual,
            entry["name"].casefold() != actual.casefold(),
            Path(entry["name"]).name != actual_base,
            Path(entry["name"]).name.casefold() != actual_base.casefold(),
            entry["name"].casefold(),
        ))
        return candidates[0]
    def _result(self, display, name, values):
        size, crc, md5, sha1 = values
        entry = self._match(name, size, crc, md5, sha1)
        if not entry:
            return {"path": display, "status": "UNMATCHED", "expected": "", "dat": ""}
        if name == entry["name"]:
            status = "OK"
        elif name.casefold() == entry["name"].casefold():
            status = "CASE_MISMATCH"
        else:
            status = "MISNAMED"
        return {"path": display, "status": status, "expected": entry["name"], "dat": entry["dat"]}
    def _scan_zip(self, path, relative):
        result = {"path": relative, "status": "OK", "members_verified": 0, "member_issues": [], "error": ""}
        try:
            with zipfile.ZipFile(str(path)) as archive:
                infos = [info for info in archive.infolist() if not info.is_dir() and Path(info.filename).suffix.lower() not in IGNORED]
                if not infos:
                    result["status"] = "EMPTY_ARCHIVE"
                    return result
                for info in infos:
                    self.check_cancelled()
                    try:
                        with archive.open(info) as stream:
                            values = self._hash(stream)
                        member = self._result(info.filename, info.filename, values)
                        if member["status"] == "OK":
                            result["members_verified"] += 1
                        else:
                            result["member_issues"].append(member)
                    except Cancelled:
                        raise
                    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
                        result["member_issues"].append({"path": info.filename, "status": "READ_ERROR", "error": str(error)})
                statuses = {item["status"] for item in result["member_issues"]}
                if "READ_ERROR" in statuses:
                    result["status"] = "READ_ERROR"
                elif "UNMATCHED" in statuses:
                    result["status"] = "UNMATCHED_MEMBERS"
                elif "MISNAMED" in statuses:
                    result["status"] = "MISNAMED_MEMBERS"
                elif "CASE_MISMATCH" in statuses:
                    result["status"] = "CASE_MISMATCH"
        except Cancelled:
            raise
        except (OSError, RuntimeError, zipfile.BadZipFile) as error:
            result["status"] = "READ_ERROR"
            result["error"] = str(error)
        return result
    def scan(self):
        root = self.detect_root()
        self.build_index()
        files = sorted(iter_processable_files(root))
        results = []
        unsupported = {".7z", ".rar", ".tar", ".gz", ".bz2", ".xz"}
        for number, path in enumerate(files, 1):
            self.check_cancelled()
            relative = str(path.relative_to(root))
            try:
                suffix = path.suffix.lower()
                if suffix == ".zip":
                    results.append(self._scan_zip(path, relative))
                elif suffix in unsupported:
                    results.append({"path": relative, "status": "UNSUPPORTED_ARCHIVE", "expected": "", "dat": ""})
                else:
                    with path.open("rb") as stream:
                        values = self._hash(stream)
                    results.append(self._result(relative, path.name, values))
            except Cancelled:
                raise
            except (OSError, RuntimeError) as error:
                results.append({"path": relative, "status": "READ_ERROR", "expected": "", "dat": "", "error": str(error)})
            self.emit("progress", number, len(files), "files")
        summary = dict(Counter(item["status"] for item in results))
        return {"report_version": 5, "type": "dat", "system": self.system, "source": str(root), "scope": "present-files-only", "summary": summary, "results": results}
class BiosValidator(DatValidator):
    """Validate BIOS and firmware files against Libretro System.dat."""
    def __init__(self, app_dir, config, emit, stop_event):
        super().__init__(app_dir, config, {}, "bios", emit, stop_event)
        self.matched = {}
        self.expected_paths = set()
        self.expected_names = set()
    def system_dat(self):
        for path in (
            self.app_dir / "dats" / "local" / "bios" / "System.dat",
            self.app_dir / "dats" / "current" / "dat" / "System.dat",
        ):
            if path.is_file():
                return path
        return None
    def dat_files(self):
        path = self.system_dat()
        if path is None:
            raise RuntimeError("System.dat was not found")
        valid, error = inspect_dat(path)
        if not valid:
            raise RuntimeError("System.dat is unusable: %s" % error)
        return [path]
    def bios_roots(self):
        roots, seen = [], set()
        for value in self.config.get("bios_roots", []):
            path = Path(value)
            if not path.is_dir():
                continue
            try:
                identity = str(path.resolve())
            except OSError:
                identity = str(path.absolute())
            if identity not in seen:
                seen.add(identity)
                roots.append(path)
        if not roots:
            raise RuntimeError("No BIOS folder was detected")
        return roots
    @staticmethod
    def _normalise_path(value):
        return str(value).replace("\\", "/").lstrip("./")
    def _matches(self, size, crc, md5, sha1):
        found, seen = [], set()
        for value, table in ((sha1, self.by_sha1), (md5, self.by_md5), (crc, self.by_crc)):
            if not value:
                continue
            for entry in table.get(value, []):
                identity = id(entry)
                if identity in seen:
                    continue
                seen.add(identity)
                if entry["size"] is not None and entry["size"] != size:
                    continue
                if entry["crc"] and entry["crc"] != crc:
                    continue
                if entry["md5"] and entry["md5"] != md5:
                    continue
                if entry["sha1"] and entry["sha1"] != sha1:
                    continue
                found.append(entry)
        return found
    def _result(self, relative, values):
        actual = self._normalise_path(relative)
        size, crc, md5, sha1 = values
        entries = self._matches(size, crc, md5, sha1)
        if not entries:
            return {"path": actual, "status": "UNMATCHED", "expected": "", "dat": "System.dat"}
        actual_path = Path(actual)
        entries.sort(key=lambda entry: (
            self._normalise_path(entry["name"]) != actual,
            Path(self._normalise_path(entry["name"])).name != actual_path.name,
            self._normalise_path(entry["name"]).casefold(),
        ))
        entry = entries[0]
        expected = self._normalise_path(entry["name"])
        expected_path = Path(expected)
        exact_path = actual == expected
        exact_name = actual_path.name == expected_path.name
        exact_parent = self._normalise_path(actual_path.parent) == self._normalise_path(expected_path.parent)
        if exact_path:
            status = "VERIFIED"
        elif exact_name:
            status = "BADPATH"
        elif exact_parent:
            status = "RENAME"
        else:
            status = "BADPATH_RENAME"
        return {"path": actual, "status": status, "expected": expected, "dat": "System.dat"}
    @staticmethod
    def _mark_duplicates(results):
        groups = {}
        for result in results:
            expected = result.get("expected")
            if expected and result.get("status") in {"VERIFIED", "BADPATH", "RENAME", "BADPATH_RENAME"}:
                groups.setdefault(expected, []).append(result)
        rank = {"VERIFIED": 0, "BADPATH": 1, "RENAME": 2, "BADPATH_RENAME": 3}
        for expected, copies in groups.items():
            if len(copies) < 2:
                continue
            canonical = min(copies, key=lambda item: (rank.get(item.get("status"), 9), str(item.get("source", "")).casefold(), str(item.get("path", "")).casefold()))
            canonical_path = canonical.get("path", expected)
            for copy in copies:
                if copy is canonical:
                    continue
                copy["status"] = "DUPLICATE"
                copy["duplicate_of"] = canonical_path
                copy["duplicate_of_source"] = canonical.get("source", copy.get("source", ""))
        return results

    def scan(self):
        dat_path = self.dat_files()[0]
        self.emit("stage", "Loading System.dat")
        self.build_index()
        self.expected_paths = {self._normalise_path(entry["name"]).casefold() for entry in self.entries}
        self.expected_names = {Path(self._normalise_path(entry["name"])).name.casefold() for entry in self.entries}
        roots = self.bios_roots()
        self.emit("stage", "Finding BIOS and firmware files")
        files = []
        for root in roots:
            for path in root.rglob("*"):
                self.check_cancelled()
                if path.is_symlink() or not path.is_file():
                    continue
                if path.name == ".gitkeep" or "__pycache__" in path.parts or path.suffix.lower() in (".pyc", ".tmp"):
                    continue
                relative = self._normalise_path(path.relative_to(root))
                explicit = relative.casefold() in self.expected_paths or path.name.casefold() in self.expected_names
                shallow = len(Path(relative).parts) <= 2
                if not explicit and (path.suffix.lower() in IGNORED or not shallow):
                    continue
                files.append((root, path))
        files.sort(key=lambda item: (str(item[0]).casefold(), str(item[1]).casefold()))
        results = []
        for number, (root, path) in enumerate(files, 1):
            self.check_cancelled()
            relative = str(path.relative_to(root))
            try:
                with path.open("rb") as stream:
                    values = self._hash(stream)
                result = self._result(relative, values)
                result["source"] = str(root)
                results.append(result)
            except Cancelled:
                raise
            except (OSError, RuntimeError) as error:
                results.append({"path": relative, "source": str(root), "status": "READ_ERROR", "expected": "", "dat": "System.dat", "error": str(error)})
            self.emit("progress", number, len(files), "BIOS files")
        self._mark_duplicates(results)
        summary = dict(Counter(item["status"] for item in results))
        return {"report_version": 5, "type": "bios", "dat": str(dat_path), "sources": [str(root) for root in roots], "summary": summary, "results": results}
    def apply_fixes(self, payload):
        changed = 0
        for result in payload.get("results", []):
            if result.get("status") not in {"BADPATH", "RENAME", "BADPATH_RENAME"}:
                continue
            root = Path(result.get("source", "")); source = root / result.get("path", ""); target = root / result.get("expected", "")
            temporary = root / (".retroscrape-fix-%d-%s" % (changed, source.name))
            try:
                root_resolved = root.resolve(); source_resolved = source.resolve()
                if root_resolved != source_resolved and root_resolved not in source_resolved.parents: raise RuntimeError("Source is outside the BIOS folder")
                if not source.is_file(): raise RuntimeError("Source file no longer exists")
                target.parent.mkdir(parents=True, exist_ok=True)
                try: same_entry = source.samefile(target)
                except (FileNotFoundError, OSError): same_entry = False
                if target.exists() and not same_entry: raise RuntimeError("Target already exists")
                original = result["path"]
                if source != target:
                    if same_entry:
                        suffix = 0
                        while temporary.exists():
                            suffix += 1; temporary = root / (".retroscrape-fix-%d-%d-%s" % (changed, suffix, source.name))
                        source.replace(temporary); temporary.replace(target)
                    else: source.replace(target)
                parent = source.parent
                while parent != root and parent.is_dir():
                    try: parent.rmdir()
                    except OSError: break
                    parent = parent.parent
                result["original_path"] = original; result["path"] = result["expected"]; result["status"] = "FIXED"; changed += 1; mark_persistent_write()
            except (OSError, RuntimeError) as error:
                if temporary.exists() and not source.exists():
                    try: temporary.replace(source)
                    except OSError: pass
                result["status"] = "FIX_ERROR"; result["error"] = str(error)
        payload["summary"] = dict(Counter(item["status"] for item in payload.get("results", []))); payload["fixes_applied"] = changed
        return changed
    def delete_duplicates(self, payload):
        deleted = 0
        for result in payload.get("results", []):
            if result.get("status") != "DUPLICATE":
                continue
            root = Path(result.get("source", ""))
            duplicate = root / result.get("path", "")
            canonical_root = Path(result.get("duplicate_of_source", result.get("source", "")))
            canonical = canonical_root / result.get("duplicate_of", result.get("expected", ""))
            try:
                root_resolved = root.resolve()
                duplicate_resolved = duplicate.resolve()
                canonical_resolved = canonical.resolve()
                if root_resolved != duplicate_resolved and root_resolved not in duplicate_resolved.parents:
                    raise RuntimeError("Duplicate is outside the BIOS folder")
                if root_resolved != canonical_resolved and root_resolved not in canonical_resolved.parents:
                    raise RuntimeError("Canonical file is outside the BIOS folder")
                if not duplicate.is_file() or not canonical.is_file():
                    raise RuntimeError("Duplicate or canonical file no longer exists")
                with duplicate.open("rb") as stream:
                    duplicate_hash = self._hash(stream)
                with canonical.open("rb") as stream:
                    canonical_hash = self._hash(stream)
                if duplicate_hash != canonical_hash:
                    raise RuntimeError("Files are no longer identical")
                duplicate.unlink()
                parent = duplicate.parent
                while parent != root and parent.is_dir():
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent
                result["status"] = "DELETED_DUPLICATE"
                deleted += 1
                mark_persistent_write()
            except (OSError, RuntimeError) as error:
                result["status"] = "DELETE_ERROR"
                result["error"] = str(error)
        payload["summary"] = dict(Counter(item["status"] for item in payload.get("results", [])))
        payload["duplicates_deleted"] = deleted
        return deleted

