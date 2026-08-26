import binascii
import json
import shutil
import hashlib
import os
import re
import signal
import subprocess
import time
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from .storage import durable_json, mark_persistent_write


class Cancelled(Exception):
    pass


class XMameValidator:
    def __init__(self, app_dir, config, emit, stop_event):
        self.app_dir = Path(app_dir)
        self.config = config
        self.emit = emit
        self.stop_event = stop_event
        self.settings = config["xmame"]
        self.xml_path = Path(self.settings["xml_path"])

    def check_cancelled(self):
        if self.stop_event.is_set():
            raise Cancelled()

    def _xml_build(self, path):
        """Read the root build attribute without parsing the large xMAME DTD."""
        pattern = re.compile(br'<mame\s+[^>]*build=["\']([^"\']+)["\']', re.IGNORECASE)
        try:
            with Path(path).open("rb") as handle:
                buffer = b""
                remaining = 4 * 1024 * 1024
                while remaining > 0:
                    block = handle.read(min(256 * 1024, remaining))
                    if not block:
                        break
                    buffer += block
                    match = pattern.search(buffer)
                    if match:
                        return match.group(1).decode("utf-8", "replace")
                    if len(buffer) > 512 * 1024:
                        buffer = buffer[-512 * 1024:]
                    remaining -= len(block)
        except OSError:
            return ""
        return ""

    def _clean_xml_file(self, path):
        """Remove a BOM or non-XML launcher output before the XML declaration."""
        path = Path(path)
        try:
            with path.open("rb") as handle:
                head = handle.read(4 * 1024 * 1024)
        except OSError:
            return False
        bom = b"\xef\xbb\xbf"
        if head.startswith(bom):
            prefix = len(bom)
        else:
            declaration = head.find(b"<?xml")
            doctype = head.find(b"<!DOCTYPE")
            root = head.find(b"<mame")
            starts = [offset for offset in (declaration, doctype, root) if offset >= 0]
            if not starts:
                return False
            prefix = min(starts)
        if prefix:
            temporary = path.with_suffix(path.suffix + ".clean")
            with path.open("rb") as source, temporary.open("wb") as target:
                source.seek(prefix)
                while True:
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    target.write(block)
                target.flush()
                os.fsync(target.fileno())
            os.replace(str(temporary), str(path))
        return True

    def _xml_parseable(self, path):
        try:
            for event, element in ET.iterparse(str(path), events=("end",)):
                if element.tag in ("game", "machine"):
                    element.clear()
            return True
        except (OSError, ET.ParseError):
            return False

    def xml_valid(self):
        if not self.xml_path.is_file() or self.xml_path.stat().st_size <= 1024:
            return False
        if not self._clean_xml_file(self.xml_path):
            return False
        return self._xml_build(self.xml_path) == self.settings["expected_build"] and self._xml_parseable(self.xml_path)

    def ensure_xml(self, force=False):
        if not force and self.xml_valid():
            return self.xml_path
        executable = Path(self.settings["executable"])
        if not executable.is_file():
            raise RuntimeError("xMAME executable not found: %s" % executable)
        self.xml_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.xml_path.with_suffix(".xml.tmp")
        error_path = self.app_dir / "logs" / "xmame-listxml.log"
        temporary.unlink(missing_ok=True)
        self.emit("stage", "Generating xMAME database")
        with temporary.open("wb") as output, error_path.open("wb") as errors:
            process = subprocess.Popen(
                [str(executable), "-listxml"],
                cwd=str(executable.parent),
                stdout=output,
                stderr=errors,
                start_new_session=True,
            )
            while process.poll() is None:
                if self.stop_event.is_set():
                    self._stop_process(process)
                    temporary.unlink(missing_ok=True)
                    raise Cancelled()
                size = temporary.stat().st_size if temporary.exists() else 0
                self.emit("activity", "Reading xMAME definitions", size)
                time.sleep(0.25)
            output.flush()
            os.fsync(output.fileno())
        if process.returncode != 0:
            temporary.unlink(missing_ok=True)
            raise RuntimeError("xMAME database generation failed; see logs/xmame-listxml.log")
        if not self._clean_xml_file(temporary):
            temporary.unlink(missing_ok=True)
            raise RuntimeError("xMAME output did not contain an XML document; see logs/xmame-listxml.log")
        build = self._xml_build(temporary)
        if build != self.settings["expected_build"]:
            size = temporary.stat().st_size
            temporary.unlink(missing_ok=True)
            raise RuntimeError("Unexpected xMAME build: %s (XML size: %d bytes; see logs/xmame-listxml.log)" % (build or "unknown", size))
        if not self._xml_parseable(temporary):
            temporary.unlink(missing_ok=True)
            raise RuntimeError("Generated xMAME XML is not well-formed; see logs/xmame-listxml.log")
        os.replace(str(temporary), str(self.xml_path))
        self.emit("stage", "xMAME database ready")
        return self.xml_path

    @staticmethod
    def _stop_process(process):
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 2.0
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    @staticmethod
    def _yes(value):
        return str(value or "").lower() in ("yes", "true", "1")

    @classmethod
    def _rom_record(cls, rom):
        return {
            "name": rom.get("name", ""),
            "size": int(rom.get("size", "0") or 0),
            "crc": rom.get("crc", "").lower(),
            "sha1": rom.get("sha1", "").lower(),
            "merge": rom.get("merge", ""),
            "status": rom.get("status", "good").lower(),
            "optional": cls._yes(rom.get("optional")),
            "bios": rom.get("bios", ""),
            "region": rom.get("region", ""),
        }

    @classmethod
    def _disk_record(cls, disk):
        return {
            "name": disk.get("name", ""),
            "sha1": disk.get("sha1", "").lower(),
            "merge": disk.get("merge", ""),
            "status": disk.get("status", "good").lower(),
            "optional": cls._yes(disk.get("optional")),
            "region": disk.get("region", ""),
        }

    @staticmethod
    def _required(record):
        return bool(record.get("name")) and record.get("status") != "nodump" and not record.get("optional")

    def load_machines(self, wanted, include_bios=False):
        machines = {}
        count = 0
        for _event, element in ET.iterparse(str(self.xml_path), events=("end",)):
            if element.tag not in ("game", "machine"):
                continue
            name = element.get("name", "")
            is_bios = element.get("isbios", "no") == "yes"
            if name in wanted or (include_bios and is_bios):
                machines[name] = {
                    "name": name,
                    "description": element.findtext("description", name),
                    "cloneof": element.get("cloneof", ""),
                    "romof": element.get("romof", ""),
                    "isbios": is_bios,
                    "roms": [self._rom_record(rom) for rom in element.findall("rom")],
                    "disks": [self._disk_record(disk) for disk in element.findall("disk")],
                }
            element.clear()
            count += 1
            if count % 500 == 0:
                self.emit("activity", "Indexing xMAME database", count)
            self.check_cancelled()
        return machines

    def _actual_member(self, archive, info, need_sha1=False):
        crc = 0
        sha1 = hashlib.sha1() if need_sha1 else None
        size = 0
        with archive.open(info) as stream:
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                crc = binascii.crc32(block, crc)
                if sha1:
                    sha1.update(block)
                self.check_cancelled()
        return size, "%08x" % (crc & 0xffffffff), sha1.hexdigest() if sha1 else ""

    def _dependency_roots(self, rom_dir):
        roots = [Path(rom_dir)]
        for value in self.config.get("bios_roots", []):
            path = Path(value)
            if path.is_dir() and path not in roots:
                roots.append(path)
        return roots

    @staticmethod
    def _available_archives(roots):
        available = {}
        for root in roots:
            if not root.is_dir():
                continue
            for path in root.glob("*.zip"):
                available.setdefault(path.stem, path)
        return available

    def _disk_state(self, rom_dir, machine):
        required = [disk for disk in machine.get("disks", []) if self._required(disk) and not disk.get("merge")]
        missing = []
        present = []
        for disk in required:
            candidates = [
                Path(rom_dir) / machine["name"] / (disk["name"] + ".chd"),
                Path(rom_dir) / (disk["name"] + ".chd"),
            ]
            found = next((path for path in candidates if path.is_file()), None)
            if found:
                present.append(str(found.relative_to(Path(rom_dir))))
            else:
                missing.append(machine["name"] + "/" + disk["name"] + ".chd")
        return present, missing

    def _check_records(self, archive, members, records):
        missing, bad, verified = [], [], []
        for record in records:
            if not self._required(record):
                continue
            info = members.get(record["name"])
            if info is None:
                missing.append(record["name"])
                continue
            actual_size, actual_crc, actual_sha1 = self._actual_member(archive, info, bool(record.get("sha1")))
            mismatch = bool(record.get("size") and actual_size != record["size"])
            mismatch = mismatch or bool(record.get("crc") and actual_crc != record["crc"])
            mismatch = mismatch or bool(record.get("sha1") and actual_sha1 != record["sha1"])
            if mismatch:
                bad.append(record["name"])
            else:
                verified.append(record["name"])
        return missing, bad, verified

    def validate_archive(self, path, machine, available_archives, rom_dir):
        own = [rom for rom in machine["roms"] if not rom.get("merge")]
        inherited = [rom for rom in machine["roms"] if rom.get("merge")]
        ignored = [rom["name"] for rom in machine["roms"] if rom.get("name") and not self._required(rom)]
        dependencies = []
        for dependency in (machine.get("cloneof", ""), machine.get("romof", "")):
            if dependency and dependency not in dependencies:
                dependencies.append(dependency)
        result = {
            "archive": path.name,
            "description": machine["description"],
            "status": "VERIFIED",
            "layout": "standalone",
            "missing": [],
            "bad": [],
            "extras": [],
            "missing_dependencies": [],
            "ignored": sorted(ignored),
            "chd_present": [],
            "chd_missing": [],
            "chd_verification": "existence-only",
            "error": "",
        }
        try:
            with zipfile.ZipFile(str(path)) as archive:
                members = {info.filename: info for info in archive.infolist() if not info.is_dir()}
                missing, bad, _verified = self._check_records(archive, members, own)
                result["missing"].extend(missing)
                result["bad"].extend(bad)

                inherited_required = [record for record in inherited if self._required(record)]
                inherited_names = {record["name"] for record in inherited_required}
                inherited_present = bool(inherited_required) and inherited_names.issubset(set(members))
                inherited_valid = False
                if inherited_present:
                    imissing, ibad, _ = self._check_records(archive, members, inherited_required)
                    inherited_valid = not imissing and not ibad
                    if ibad:
                        result["bad"].extend(ibad)

                if inherited_valid:
                    result["layout"] = "full-non-merged"
                elif dependencies:
                    missing_dependencies = []
                    dependency_bad = []
                    remaining = list(inherited_required)
                    for dependency in dependencies:
                        dependency_path = available_archives.get(dependency)
                        if dependency_path is None:
                            continue
                        try:
                            with zipfile.ZipFile(str(dependency_path)) as dependency_archive:
                                dependency_members = {info.filename: info for info in dependency_archive.infolist() if not info.is_dir()}
                                next_remaining = []
                                for record in remaining:
                                    member_name = record.get("merge") or record["name"]
                                    info = dependency_members.get(member_name)
                                    if info is None:
                                        next_remaining.append(record)
                                        continue
                                    expected = dict(record)
                                    expected["name"] = member_name
                                    _missing, bad, _verified = self._check_records(dependency_archive, dependency_members, [expected])
                                    if bad:
                                        dependency_bad.append(dependency + ".zip:" + member_name)
                                remaining = next_remaining
                        except (OSError, zipfile.BadZipFile, RuntimeError) as error:
                            dependency_bad.append(dependency + ".zip:" + str(error))
                    if missing_dependencies or remaining or dependency_bad:
                        result["layout"] = "dependency-missing"
                        result["missing_dependencies"] = missing_dependencies + ["dependency member: " + (record.get("merge") or record["name"]) for record in remaining]
                        result["bad"].extend(dependency_bad)
                    else:
                        result["layout"] = "split"
                elif inherited_required:
                    result["layout"] = "non-merged"

                known_names = set()
                for rom in machine["roms"]:
                    if rom.get("name"):
                        known_names.add(rom["name"])
                    if rom.get("merge"):
                        known_names.add(rom["merge"])
                result["extras"] = sorted(name for name in members if name not in known_names)
        except Cancelled:
            raise
        except (OSError, zipfile.BadZipFile, RuntimeError) as error:
            result["status"] = "READ_ERROR"
            result["error"] = str(error)
            return result

        present, missing_chd = self._disk_state(rom_dir, machine)
        result["chd_present"] = present
        result["chd_missing"] = missing_chd

        if result["bad"]:
            result["status"] = "CHECKSUM_MISMATCH"
        elif result["missing"] or result["chd_missing"]:
            result["status"] = "INCOMPLETE"
        elif result["missing_dependencies"]:
            result["status"] = "VERIFIED_NEEDS_PARENT"
        elif result["extras"]:
            result["status"] = "VERIFIED_WITH_EXTRAS"
        return result

    def scan_directory(self, rom_dir, bios_only=False):
        rom_dir = Path(rom_dir)
        if not rom_dir.is_dir():
            raise RuntimeError("Folder not found: %s" % rom_dir)
        self.ensure_xml()
        archives = sorted(rom_dir.glob("*.zip"))
        wanted = {path.stem for path in archives}
        machines = self.load_machines(wanted, include_bios=bios_only)
        if bios_only:
            machines = {name: machine for name, machine in machines.items() if machine["isbios"]}
            archives = [path for path in archives if path.stem in machines]
        roots = self._dependency_roots(rom_dir)
        available = self._available_archives(roots)
        results = []
        for index, path in enumerate(archives, 1):
            self.check_cancelled()
            machine = machines.get(path.stem)
            if machine is None:
                results.append({
                    "archive": path.name, "description": "", "status": "UNSUPPORTED",
                    "layout": "unknown", "missing": [], "bad": [], "extras": [],
                    "missing_dependencies": [], "ignored": [], "chd_present": [],
                    "chd_missing": [], "error": "Not present in xMAME 0.106",
                })
            else:
                results.append(self.validate_archive(path, machine, available, rom_dir))
            self.emit("progress", index, len(archives), "BIOS archives" if bios_only else "MAME archives")
        summary = dict(Counter(item["status"] for item in results))
        return results, summary

    def scan_games(self):
        results, summary = self.scan_directory(self.settings["rom_dir"])
        return self._report_payload("mame", self.settings["rom_dir"], results, summary)

    def scan_bios(self):
        roots, seen = [], set()
        for value in self.config.get("bios_roots", []):
            path = Path(value)
            if path.is_dir():
                resolved = str(path.resolve())
                if resolved not in seen:
                    seen.add(resolved)
                    roots.append(path)
        if not roots:
            raise RuntimeError("No BIOS folder was detected")
        self.ensure_xml()
        archives = []
        for root in roots:
            archives.extend(sorted(root.glob("*.zip")))
        wanted = {path.stem for path in archives}
        machines = self.load_machines(wanted, include_bios=True)
        machines = {name: machine for name, machine in machines.items() if machine["isbios"]}
        available = self._available_archives(roots + self._dependency_roots(roots[0]))
        results = []
        for index, path in enumerate(archives, 1):
            self.check_cancelled()
            machine = machines.get(path.stem)
            if machine is None:
                result = {"archive": path.name, "description": "", "status": "UNSUPPORTED", "layout": "unknown", "missing": [], "bad": [], "extras": [], "missing_dependencies": [], "ignored": [], "chd_present": [], "chd_missing": [], "error": "Not present as BIOS in xMAME 0.106"}
            else:
                result = self.validate_archive(path, machine, available, path.parent)
            result["source"] = str(path.parent)
            results.append(result)
            self.emit("progress", index, len(archives), "BIOS archives")
        summary = dict(Counter(item["status"] for item in results))
        return self._report_payload("bios", [str(root) for root in roots], results, summary)

    def _report_payload(self, report_type, source, results, summary):
        return {"report_version": 5, "type": report_type, "build": self.settings["expected_build"], "source": str(source), "summary": summary, "results": results}

class ArcadeOrganiser:
    """Preview and apply conservative playlist-backed arcade folder moves."""

    DESTINATIONS = {
        "FBNeo - Arcade Games": "FBNEO",
        "FBNeo - Arcade Games.lpl": "FBNEO",
    }

    def __init__(self, app_dir, config, emit, stop_event):
        self.app_dir = Path(app_dir)
        self.config = config
        self.emit = emit
        self.stop_event = stop_event
        self.data_dir = self.app_dir / "data"
        self.journal = self.data_dir / "arcade-migration.json"
        self.backup_dir = self.data_dir / "arcade-migration-backup"

    def check_cancelled(self):
        if self.stop_event.is_set():
            raise Cancelled()

    @staticmethod
    def _parse_config(path):
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
    def _retroarch_process():
        proc = Path("/proc")
        if not proc.is_dir():
            return None, None
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                command = [part.decode("utf-8", "replace") for part in (entry / "cmdline").read_bytes().split(b"\0") if part]
            except OSError:
                continue
            if not command or Path(command[0]).name != "retroarch":
                continue
            config = None
            for index, value in enumerate(command[:-1]):
                if value in ("-c", "--config"):
                    config = Path(command[index + 1])
                    break
            try:
                binary = (entry / "exe").resolve()
            except OSError:
                binary = Path(command[0])
            return config, binary
        return None, None

    def _retroarch_paths(self):
        config_path = Path("/.config/retroarch/retroarch.cfg")
        binary = Path("/mnt/vendor/deep/retro/retroarch")
        if not config_path.is_file() or not binary.exists():
            raise RuntimeError("RetroArch configuration could not be resolved")
        values = self._parse_config(config_path)
        root = binary.resolve().parent
        value = values.get("playlist_directory", ":/playlists")
        if value.startswith(":/"):
            playlist_root = root / value[2:]
        elif value == ":":
            playlist_root = root
        elif value.startswith("~/") and str(config_path).startswith("/.config/"):
            playlist_root = Path("/") / value[2:]
        else:
            playlist_root = Path(value)
        if not playlist_root.is_dir():
            raise RuntimeError("RetroArch playlist directory not found: %s" % playlist_root)
        return config_path, binary, playlist_root

    @staticmethod
    def _path_identity(value):
        path = Path(str(value))
        try:
            return str(path.resolve())
        except OSError:
            return str(path.absolute())

    @staticmethod
    def _database_identity(item, playlist):
        value = str(item.get("db_name", "") or playlist.name)
        return value[:-4] if value.casefold().endswith(".lpl") else value

    def _load_playlists(self, playlist_root):
        documents = {}
        reverse = {}
        errors = []
        for path in sorted(playlist_root.rglob("*.lpl")):
            self.check_cancelled()
            try:
                document = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
                items = document.get("items", [])
                if not isinstance(items, list):
                    raise ValueError("items is not a list")
            except (OSError, ValueError) as error:
                errors.append({"playlist": str(path), "error": str(error)})
                continue
            documents[str(path)] = document
            for item in items:
                if not isinstance(item, dict) or not item.get("path"):
                    continue
                key = self._path_identity(item["path"])
                reverse.setdefault(key, []).append({
                    "playlist": str(path),
                    "database": self._database_identity(item, path),
                    "label": str(item.get("label", "")),
                    "core_name": str(item.get("core_name", "")),
                    "core_path": str(item.get("core_path", "")),
                })
        return documents, reverse, errors

    def scan(self, directory):
        source_root = Path(directory).resolve()
        _config, _binary, playlist_root = self._retroarch_paths()
        _documents, reverse, playlist_errors = self._load_playlists(playlist_root)
        results = []
        files = sorted(path for path in source_root.glob("*.zip") if path.is_file())
        rom_roots = [Path(value) for value in self.config.get("rom_roots", []) if Path(value).is_dir()]
        destination_root = next((root for root in rom_roots if source_root == root or root in source_root.parents), source_root.parent)
        for index, source in enumerate(files, 1):
            self.check_cancelled()
            key = self._path_identity(source)
            references = reverse.get(key, [])
            supported = []
            destinations = set()
            for reference in references:
                folder = self.DESTINATIONS.get(reference["database"])
                if folder:
                    target = destination_root / folder / source.name
                    supported.append(dict(reference, target=str(target)))
                    destinations.add(str(target))
            if not references:
                status = "PLAYLIST_NOT_FOUND"
                target = ""
                reason = "No exact RetroArch playlist entry"
            elif len(destinations) > 1:
                status = "CONFLICTING_PLAYLISTS"
                target = ""
                reason = "Supported playlists imply different destinations"
            elif not supported:
                status = "UNSUPPORTED_DESTINATION"
                target = ""
                reason = "Playlist database has no confirmed folder rule"
            else:
                target = next(iter(destinations))
                target_path = Path(target)
                if source.parent == target_path.parent:
                    status = "CORRECT_LOCATION"
                    reason = supported[0]["database"]
                elif target_path.exists():
                    status = "TARGET_EXISTS"
                    reason = "Destination already exists"
                else:
                    status = "MOVE_RECOMMENDED"
                    reason = supported[0]["database"]
            results.append({
                "path": str(source), "name": source.name, "status": status,
                "target": target, "reason": reason, "references": references,
            })
            self.emit("progress", index, len(files), "Checking folder placement")
        summary = dict(Counter(item["status"] for item in results))
        return {
            "type": "arcade-organisation", "source": str(source_root),
            "playlist_root": str(playlist_root), "summary": summary,
            "results": results, "playlist_errors": playlist_errors,
        }

    def _write_journal(self, payload):
        durable_json(self.journal, payload)

    def _backup_playlists(self, paths):
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        backups = {}
        for number, path_value in enumerate(sorted(paths), 1):
            path = Path(path_value)
            target = self.backup_dir / ("%03d-%s" % (number, path.name))
            with path.open("rb") as source, target.open("wb") as output:
                while True:
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            backups[str(path)] = str(target)
        return backups

    def apply(self, payload):
        suggestions = [item for item in payload.get("results", []) if item.get("status") == "MOVE_RECOMMENDED"]
        if not suggestions:
            return {"moved": 0, "status": "Nothing to move"}
        _config, _binary, playlist_root = self._retroarch_paths()
        documents, _reverse, errors = self._load_playlists(playlist_root)
        if errors:
            raise RuntimeError("A RetroArch playlist is invalid; no files were changed")
        moves = []
        affected = set()
        for item in suggestions:
            source = Path(item["path"])
            target = Path(item["target"])
            if not source.is_file():
                raise RuntimeError("Source is missing: %s" % source)
            if target.exists():
                raise RuntimeError("Destination already exists: %s" % target)
            source_references = set()
            for playlist_path, document in documents.items():
                for entry in document.get("items", []):
                    if isinstance(entry, dict) and self._path_identity(entry.get("path", "")) == self._path_identity(source):
                        source_references.add(playlist_path)
                        affected.add(playlist_path)
            if not source_references:
                raise RuntimeError("No playlist reference exists for: %s" % source)
            moves.append({"source": str(source), "target": str(target)})
        for playlist_path in affected:
            if not os.access(str(Path(playlist_path).parent), os.W_OK):
                raise RuntimeError("Playlist directory is not writable: %s" % Path(playlist_path).parent)
        backups = self._backup_playlists(affected)
        journal = {"status": "prepared", "moves": moves, "completed": [], "backups": backups}
        self._write_journal(journal)
        try:
            for index, move in enumerate(moves, 1):
                self.check_cancelled()
                source = Path(move["source"]); target = Path(move["target"])
                target.parent.mkdir(parents=True, exist_ok=True)
                source.replace(target)
                journal["completed"].append(move)
                journal["status"] = "moving"
                self._write_journal(journal)
                self.emit("progress", index, len(moves), "Moving suggested ROMs")
            replacements = {self._path_identity(move["source"]): move["target"] for move in moves}
            for playlist_path in affected:
                document = documents[playlist_path]
                for entry in document.get("items", []):
                    identity = self._path_identity(entry.get("path", "")) if isinstance(entry, dict) else ""
                    if identity in replacements:
                        entry["path"] = replacements[identity]
                path = Path(playlist_path)
                durable_json(path, document)
            missing = [move["target"] for move in moves if not Path(move["target"]).is_file()]
            if missing:
                raise RuntimeError("Moved files could not be verified: %s" % ", ".join(missing))
            mark_persistent_write()
            self.journal.unlink(missing_ok=True)
            return {"moved": len(moves), "status": "Complete", "moves": moves}
        except Exception:
            self._rollback(journal)
            raise

    def _rollback(self, journal):
        errors = []
        for path_value, backup_value in journal.get("backups", {}).items():
            try:
                source = Path(backup_value); target = Path(path_value)
                with source.open("rb") as handle, target.open("wb") as output:
                    while True:
                        block = handle.read(1024 * 1024)
                        if not block:
                            break
                        output.write(block)
                    output.flush(); os.fsync(output.fileno())
            except OSError as error:
                errors.append(str(error))
        for move in reversed(journal.get("completed", [])):
            try:
                source = Path(move["source"]); target = Path(move["target"])
                if target.exists() and not source.exists():
                    source.parent.mkdir(parents=True, exist_ok=True)
                    target.replace(source)
            except OSError as error:
                errors.append(str(error))
        journal["status"] = "rollback-incomplete" if errors else "rolled-back"
        journal["errors"] = errors
        self._write_journal(journal)
        if errors:
            raise RuntimeError("Arcade migration rollback was incomplete: %s" % "; ".join(errors))

    def recover(self):
        if not self.journal.is_file():
            return
        try:
            journal = json.loads(self.journal.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise RuntimeError("Arcade migration journal is unreadable: %s" % error)
        if journal.get("status") in ("prepared", "moving", "rollback-incomplete"):
            self._rollback(journal)
