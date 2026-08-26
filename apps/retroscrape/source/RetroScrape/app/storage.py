import json
import os
from pathlib import Path

WRITE_MARKER = Path("/tmp/retroscrape/persistent-write")

def mark_persistent_write():
    try:
        WRITE_MARKER.parent.mkdir(parents=True, exist_ok=True)
        WRITE_MARKER.touch()
    except OSError:
        pass

def write_bytes_if_changed(path, content):
    path = Path(path)
    content = bytes(content)
    try:
        if path.is_file() and path.read_bytes() == content:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".rs-write.tmp")
    suffix = 0
    while temporary.exists():
        suffix += 1
        temporary = path.with_name(".rs-write-%d.tmp" % suffix)
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
        mark_persistent_write()
        return True
    finally:
        temporary.unlink(missing_ok=True)

def durable_json(path, payload):
    content = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    return write_bytes_if_changed(path, content)
