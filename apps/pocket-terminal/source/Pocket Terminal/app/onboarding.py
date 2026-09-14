from pathlib import Path


class OnboardingState:
    """Persistent first-run state without adding another visible root file."""

    def __init__(self, path):
        self.path = Path(path)

    @property
    def completed(self):
        try:
            return self.path.read_text(encoding="utf-8").strip() == "1"
        except OSError:
            return False

    def complete(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text("1\n", encoding="utf-8")
        temporary.replace(self.path)
