import json
import uuid
from pathlib import Path
from log_writer import read_section
from typesafe_sdk import Choice, Noul, TypeSafeClient


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


NEW_OPTION = "new"
NEW_OPTION_DESCRIPTION = "This does not match any of the other items — it is a new task not previously listed."
MATCH_CONFIDENCE_FLOOR = 0.5
RECURRING_THRESHOLD = 0.5


def match_items(api_key: str, today_items: list[str], open_items: list[dict]) -> dict[str, dict]:
    """Match each today_item against open_items via one Jev Choice call.

    Returns {today_text: {"matched_id": id_or_None, "confidence": float}}.
    Skips the API call entirely when either list is empty.
    """
    if not today_items:
        return {}
    if not open_items:
        return {text: {"matched_id": None, "confidence": 1.0} for text in today_items}

    criteria = {item["id"]: item["text"] for item in open_items}
    criteria[NEW_OPTION] = NEW_OPTION_DESCRIPTION

    questions = {
        f"today_{i}": Choice(
            instructions={
                "today_item": text,
                "question": "Which existing open item does `today_item` refer to, if any?",
            },
            criteria=criteria,
        )
        for i, text in enumerate(today_items)
    }

    client = TypeSafeClient(api_key=api_key, model="jev-latest", timeout=60.0)
    result = client.system_one({}, questions)

    matches = {}
    for i, text in enumerate(today_items):
        answer = result.choices[f"today_{i}"]
        matched_id = None if answer.choice == NEW_OPTION else answer.choice
        if matched_id is not None and answer.confidence < MATCH_CONFIDENCE_FLOOR:
            matched_id = None  # low-confidence guess — treat as new rather than risk a bad merge
        matches[text] = {"matched_id": matched_id, "confidence": answer.confidence}
    return matches


def filter_recurring(api_key: str, candidate_items: list[str]) -> tuple[list[str], list[str]]:
    """Split candidate new items into (non_recurring, recurring). No API call if empty.

    Only call this with items match_items found no match for — a recurring
    item only needs judging once, the day it's first seen.
    """
    if not candidate_items:
        return [], []

    questions = {
        f"cand_{i}": Noul(
            instructions={
                "item": text,
                "question": (
                    "Is `item` a recurring or standing task, or a scheduled meeting, "
                    "rather than a one-off carry-forward item?"
                ),
            },
        )
        for i, text in enumerate(candidate_items)
    }
    client = TypeSafeClient(api_key=api_key, model="jev-latest", timeout=60.0)
    result = client.system_one({}, questions)

    non_recurring, recurring = [], []
    for i, text in enumerate(candidate_items):
        score = result.nouls[f"cand_{i}"].noul
        if score > RECURRING_THRESHOLD:
            recurring.append(text)
            print(f"skipped as recurring (noul={score:.2f}): {text}")
        else:
            non_recurring.append(text)
    return non_recurring, recurring


def update_state(state: dict, today: str, today_items: list[str], matches: dict[str, dict],
                  non_recurring_new_texts: list[str]) -> dict:
    items_by_id = {item["id"]: dict(item) for item in state["items"]}  # copy, don't mutate input
    matched_ids = set()
    non_recurring_set = set(non_recurring_new_texts)

    for text in today_items:
        match = matches.get(text, {"matched_id": None, "confidence": 0.0})
        matched_id = match["matched_id"]
        if matched_id is not None and matched_id in items_by_id:
            items_by_id[matched_id]["last_seen"] = today
            matched_ids.add(matched_id)
        elif text in non_recurring_set:
            new_id = uuid.uuid4().hex[:8]
            items_by_id[new_id] = {
                "id": new_id, "text": text, "first_seen": today, "last_seen": today,
                "resolved": False, "resolved_date": None,
            }
            matched_ids.add(new_id)
        # else: unmatched and filtered as recurring — never tracked.

    if today_items:  # only resolve when today actually had items to compare against
        for item_id, item in items_by_id.items():
            if not item["resolved"] and item_id not in matched_ids:
                item["resolved"] = True
                item["resolved_date"] = today

    return {"items": list(items_by_id.values())}
