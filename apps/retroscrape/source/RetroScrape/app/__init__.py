from pathlib import Path


def get_version():
    try:
        return (Path(__file__).resolve().parents[1] / "VERSION").read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


__version__ = get_version()
USER_AGENT = "RetroScrape/%s" % __version__
