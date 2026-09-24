import json
from pathlib import Path


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"items": []}
    return json.loads(path.read_text())


def save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, indent=2) + "\n")
