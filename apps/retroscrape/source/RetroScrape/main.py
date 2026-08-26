#!/usr/bin/env python3
import sys
import traceback
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
LOG_DIR = APP_DIR / "logs"
BOOT_LOG = LOG_DIR / "boot.log"


def write_boot_error():
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        BOOT_LOG.write_text(traceback.format_exc(), encoding="utf-8")
    except Exception:
        pass


def main():
    try:
        from app.config import load_config
        config, warning = load_config(APP_DIR)
        import json
        systems = json.loads((APP_DIR / "config" / "systems.json").read_text(encoding="utf-8"))
        if config.get("selected_system") not in systems:
            config["selected_system"] = next(iter(systems))
        from app.ui import GUI
        return GUI(APP_DIR, config, systems, warning).run()
    except Exception:
        write_boot_error()
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
