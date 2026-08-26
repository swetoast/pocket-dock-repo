import errno
import os
import shutil
import tempfile
import urllib.request
import zipfile
import time
from pathlib import Path, PurePosixPath

from .storage import durable_json, mark_persistent_write
from . import USER_AGENT


class DatabaseUpdater:
    SAFETY_MARGIN = 32 * 1024 * 1024

    def __init__(self, app_dir, config, emit, stop_event):
        self.app_dir = Path(app_dir)
        self.config = config
        self.emit = emit
        self.stop_event = stop_event
        self.dats = self.app_dir / "dats"
        self.current = self.dats / "current"
        self.candidate = self.dats / ".candidate"
        self.previous = self.dats / ".previous"
        self.journal = self.dats / ".update-state.json"

    def check_cancelled(self):
        if self.stop_event.is_set():
            raise RuntimeError("Database update cancelled")

    @staticmethod
    def _safe_member(name):
        path = PurePosixPath(name)
        return not path.is_absolute() and ".." not in path.parts and not str(name).startswith(("/", "\\"))

    @staticmethod
    def _is_symlink(item):
        return ((item.external_attr >> 16) & 0o170000) == 0o120000

    @staticmethod
    def _free_bytes(path):
        stats = os.statvfs(str(path))
        return stats.f_bavail * stats.f_frsize

    @staticmethod
    def _mount_options(path):
        target = str(Path(path).resolve())
        best_mount, best_options = "", set()
        try:
            for line in Path("/proc/mounts").read_text(encoding="utf-8").splitlines():
                fields = line.split()
                if len(fields) < 4:
                    continue
                mount = fields[1].replace("\\040", " ")
                if target == mount or target.startswith(mount.rstrip("/") + "/"):
                    if len(mount) > len(best_mount):
                        best_mount, best_options = mount, set(fields[3].split(","))
        except OSError:
            pass
        return best_mount, best_options

    def _check_storage(self, path, required=0):
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        _mount, options = self._mount_options(path)
        if "ro" in options:
            raise RuntimeError("Storage is read-only: %s" % path)
        test = path / (".retroscrape-write-test-%d" % os.getpid())
        try:
            with test.open("wb") as handle:
                handle.write(b"ok")
                handle.flush()
                os.fsync(handle.fileno())
            test.unlink()
        except OSError as error:
            if error.errno in (errno.EROFS, errno.EIO, errno.ENOSPC):
                raise RuntimeError("Storage is not safely writable: %s" % error)
            raise
        if required and self._free_bytes(path) < required:
            raise RuntimeError("Not enough free space on %s" % path)

    def recover(self):
        if not self.journal.exists():
            return
        try:
            if not self.current.exists() and self.previous.exists():
                os.replace(str(self.previous), str(self.current))
            if self.candidate.exists():
                shutil.rmtree(self.candidate)
            self.journal.unlink(missing_ok=True)
        except OSError as error:
            raise RuntimeError("Database recovery failed: %s" % error)

    @staticmethod
    def _dat_members(archive):
        selected = []
        for item in archive.infolist():
            path = PurePosixPath(item.filename)
            parts = [part.lower() for part in path.parts]
            if item.is_dir() or item.filename.endswith("/"):
                continue
            if not DatabaseUpdater._safe_member(item.filename) or DatabaseUpdater._is_symlink(item):
                raise RuntimeError("Database archive contains unsafe paths")
            if path.suffix.lower() == ".dat" and "dat" in parts:
                selected.append(item)
        return selected

    @staticmethod
    def _copy_tree(source, destination, stop_event, emit):
        files = [path for path in source.rglob("*.dat") if path.is_file()]
        for index, source_file in enumerate(files, 1):
            if stop_event.is_set():
                raise RuntimeError("Database update cancelled")
            relative = source_file.relative_to(source)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with source_file.open("rb") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
                dst.flush()
                os.fsync(dst.fileno())
            if index % 50 == 0 or index == len(files):
                emit("progress", index, len(files), "Installing databases")

    def run(self):
        settings = self.config["databases"]
        self.dats.mkdir(parents=True, exist_ok=True)
        self.recover()
        self._check_storage(self.dats)
        stage_root = Path(tempfile.mkdtemp(prefix="retroscrape-db-", dir="/tmp"))
        archive_path = stage_root / "database.zip"
        extracted = stage_root / "dat"
        extracted.mkdir()
        try:
            self.emit("stage", "Connecting")
            request = urllib.request.Request(settings["url"], headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=int(settings.get("timeout", 30))) as response:
                total = int(response.headers.get("Content-Length", "0") or 0)
                self._check_storage(stage_root, (total * 2 if total else self.SAFETY_MARGIN) + self.SAFETY_MARGIN)
                with archive_path.open("wb") as output:
                    downloaded = 0
                    while True:
                        self.check_cancelled()
                        block = response.read(256 * 1024)
                        if not block:
                            break
                        output.write(block)
                        downloaded += len(block)
                        self.emit("progress", downloaded, total, "Downloading databases")
                    output.flush()
                    os.fsync(output.fileno())
            self.emit("stage", "Checking download")
            if not zipfile.is_zipfile(archive_path):
                raise RuntimeError("Downloaded database archive is invalid")
            with zipfile.ZipFile(archive_path) as archive:
                members = self._dat_members(archive)
                if not members:
                    raise RuntimeError("Downloaded archive contains no usable DAT files")
                unpacked = sum(item.file_size for item in members)
                self._check_storage(stage_root, unpacked + self.SAFETY_MARGIN)
                self.emit("stage", "Extracting databases")
                for index, item in enumerate(members, 1):
                    self.check_cancelled()
                    source = archive.open(item)
                    original_parts = PurePosixPath(item.filename).parts
                    lower_parts = [part.lower() for part in original_parts]
                    relative = PurePosixPath(*original_parts[lower_parts.index("dat"):])
                    target = extracted / Path(*relative.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with source, target.open("wb") as output:
                        shutil.copyfileobj(source, output, 1024 * 1024)
                    if index % 100 == 0 or index == len(members):
                        self.emit("progress", index, len(members), "Extracting databases")
            self.emit("stage", "Checking DAT files")
            files = list(extracted.rglob("*.dat"))
            if not files:
                raise RuntimeError("No DAT files were extracted")
            required_target = sum(path.stat().st_size for path in files) + self.SAFETY_MARGIN
            self._check_storage(self.dats, required_target)
            if self.candidate.exists():
                shutil.rmtree(self.candidate)
            self.emit("stage", "Installing databases")
            self._copy_tree(extracted, self.candidate, self.stop_event, self.emit)
            durable_json(self.journal, {"phase": "candidate-ready"})
            if self.previous.exists():
                shutil.rmtree(self.previous)
            if self.current.exists():
                os.replace(str(self.current), str(self.previous))
            durable_json(self.journal, {"phase": "current-moved"})
            os.replace(str(self.candidate), str(self.current))
            durable_json(self.current / ".retroscrape-manifest.json", {
                "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "source_url": settings["url"],
                "dat_count": len(files),
                "total_size": sum(path.stat().st_size for path in files),
            })
            if not self.current.is_dir() or not list(self.current.rglob("*.dat")):
                raise RuntimeError("Installed database could not be verified")
            mark_persistent_write()
            self.journal.unlink(missing_ok=True)
            self.emit("stage", "Database update complete")
            return self.current
        except OSError as error:
            if error.errno in (errno.EROFS, errno.EIO, errno.ENOSPC):
                raise RuntimeError("Storage failure during database update: %s" % error)
            raise
        finally:
            shutil.rmtree(stage_root, ignore_errors=True)

    def cleanup_previous(self):
        self._check_storage(self.dats)
        if self.previous.exists():
            shutil.rmtree(self.previous)
        self.journal.unlink(missing_ok=True)
