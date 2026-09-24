import json
from pathlib import Path
from log_writer import read_section


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"items": []}
    return json.loads(path.read_text())


def save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, indent=2) + "\n")


def extract_tomorrow_items(log_text: str) -> list[str]:
    section = read_section(log_text, "Tomorrow")
    items = []
    for line in section.splitlines():
        line = line.strip()
        if line.startswith("- [ ]"):
            text = line[len("- [ ]"):].strip()
            if text:
                items.append(text)
    return items
