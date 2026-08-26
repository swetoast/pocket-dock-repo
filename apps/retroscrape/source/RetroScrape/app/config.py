import json
import shutil
import time
from pathlib import Path

from .storage import durable_json


def _merge(defaults, user):
    result = dict(defaults)
    if not isinstance(user, dict):
        return result
    for key, value in user.items():
        if key in defaults and isinstance(defaults[key], dict) and isinstance(value, dict):
            result[key] = _merge(defaults[key], value)
        else:
            result[key] = value
    return result


def _normalise(config, defaults):
    changed = False
    if config.get("config_version") != defaults.get("config_version"):
        config["config_version"] = defaults.get("config_version", 6)
        changed = True
    for section in ("xmame", "thegamesdb", "databases"):
        if not isinstance(config.get(section), dict):
            config[section] = dict(defaults[section])
            changed = True
    if not isinstance(config.get("rom_roots"), list):
        config["rom_roots"] = list(defaults["rom_roots"])
        changed = True
    if not isinstance(config.get("selected_system"), str):
        config["selected_system"] = defaults["selected_system"]
        changed = True
    if not isinstance(config.get("show_missing_systems"), bool):
        config["show_missing_systems"] = False
        changed = True
    if not isinstance(config["databases"].get("url"), str) or not config["databases"].get("url"):
        config["databases"]["url"] = defaults["databases"]["url"]
        changed = True
    try:
        config["databases"]["timeout"] = max(5, int(config["databases"].get("timeout", 30)))
    except (TypeError, ValueError):
        config["databases"]["timeout"] = 30
        changed = True
    if not isinstance(config.get("bios_roots"), list):
        config["bios_roots"] = list(defaults["bios_roots"])
        changed = True
    if not isinstance(config["thegamesdb"].get("enabled"), bool):
        config["thegamesdb"]["enabled"] = False
        changed = True
    if not isinstance(config["thegamesdb"].get("api_key_file"), str) or not config["thegamesdb"].get("api_key_file"):
        config["thegamesdb"]["api_key_file"] = defaults["thegamesdb"]["api_key_file"]
        changed = True
    try:
        config["thegamesdb"]["timeout"] = max(5, int(config["thegamesdb"].get("timeout", 30)))
        config["thegamesdb"]["cache_days"] = max(1, int(config["thegamesdb"].get("cache_days", 30)))
        config["thegamesdb"]["max_hash_mib"] = max(1, int(config["thegamesdb"].get("max_hash_mib", 128)))
        config["thegamesdb"]["reserve_requests"] = max(0, int(config["thegamesdb"].get("reserve_requests", 10)))
        config["thegamesdb"]["max_retries"] = max(0, min(8, int(config["thegamesdb"].get("max_retries", 4))))
        config["thegamesdb"]["backoff_seconds"] = max(1, int(config["thegamesdb"].get("backoff_seconds", 2)))
        config["thegamesdb"]["max_backoff_seconds"] = max(5, int(config["thegamesdb"].get("max_backoff_seconds", 60)))
    except (TypeError, ValueError):
        config["thegamesdb"]["timeout"] = 30
        config["thegamesdb"]["cache_days"] = 30
        config["thegamesdb"]["max_hash_mib"] = 128
        config["thegamesdb"]["reserve_requests"] = 10
        config["thegamesdb"]["max_retries"] = 4
        config["thegamesdb"]["backoff_seconds"] = 2
        config["thegamesdb"]["max_backoff_seconds"] = 60
        changed = True
    if not isinstance(config["thegamesdb"].get("download_boxart"), bool):
        config["thegamesdb"]["download_boxart"] = True
        changed = True
    for key in ("executable", "rom_dir", "xml_path", "expected_build"):
        if not isinstance(config["xmame"].get(key), str) or not config["xmame"].get(key):
            config["xmame"][key] = defaults["xmame"][key]
            changed = True
    return config, changed


def load_config(app_dir):
    app_dir = Path(app_dir)
    config_dir = app_dir / "config"
    defaults = json.loads((config_dir / "defaults.json").read_text(encoding="utf-8"))
    config_path = config_dir / "config.json"
    warning = None
    repaired = False
    if not config_path.exists():
        user = {}
    else:
        try:
            user = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(user, dict):
                raise ValueError("configuration root is not an object")
        except (OSError, ValueError, TypeError):
            user = {}
            repaired = True
            warning = "Invalid configuration was backed up and reset."
            backup = config_path.with_name("config.invalid-%d.json" % int(time.time()))
            try:
                shutil.copy2(str(config_path), str(backup))
            except OSError as error:
                warning = "Invalid configuration was reset but could not be backed up: %s" % error
    config = _merge(defaults, user)
    if "skyscraper" in config:
        config.pop("skyscraper", None)
        repaired = True
    config, changed = _normalise(config, defaults)
    if repaired or changed or not config_path.exists():
        try:
            durable_json(config_path, config)
        except OSError as error:
            message = "Configuration is usable in memory but could not be saved: %s" % error
            warning = ((warning + " ") if warning else "") + message
    return config, warning
