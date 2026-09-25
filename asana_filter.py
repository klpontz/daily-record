import re

from asana_client import project_name
from typesafe_sdk import Noul, TypeSafeClient

NOISE_THRESHOLD = 0.5


def filter_items(api_key: str, tasks: list[dict], comments: list[dict]) -> tuple[list[dict], list[dict]]:
    """Keep only substantive tasks/comments, judged by one Jev Noul per item.

    Every question carries its own item's data directly in a structured
    `instructions` object — no array-index lookup into a shared state blob.
    One API call total. Short-circuits with no API call when both lists
    are empty. Prints every dropped item with its score, so a day where
    everything scored low doesn't look identical to a day with no activity.
    """
    if not tasks and not comments:
        return [], []

    questions = {}
    for i, t in enumerate(tasks):
        questions[f"task_{i}"] = Noul(
            instructions={
                "item": {"name": t["name"], "project": project_name(t)},
                "question": (
                    "Is `item` substantive work worth surfacing in a career log "
                    "— not a trivially small task or routine admin note?"
                ),
            },
        )
    for i, c in enumerate(comments):
        questions[f"comment_{i}"] = Noul(
            instructions={
                "item": {
                    "task": c["task"],
                    "project": c.get("project"),
                    "task_description": c.get("task_description", ""),
                    "prior_comments": c.get("prior_comments", []),
                    "comment": c["comment"],
                },
                "question": (
                    "`item.comment` was written by the log owner on an Asana task. "
                    "`item.prior_comments` are the thread leading up to it, and "
                    "`item.task_description` is the original request. Does writing this "
                    "comment reflect real work — a review, approval, decision, direction, "
                    "or substantive answer — rather than a routine acknowledgment, "
                    "self-assignment, or scheduling note?"
                ),
            },
        )

    client = TypeSafeClient(api_key=api_key, model="jev-latest", timeout=60.0)
    result = client.system_one({}, questions)

    kept_tasks = []
    for i, t in enumerate(tasks):
        score = result.nouls[f"task_{i}"].noul
        if score > NOISE_THRESHOLD:
            kept_tasks.append(t)
        else:
            print(f"dropped (noul={score:.2f}): {t['name']}")

    kept_comments = []
    for i, c in enumerate(comments):
        score = result.nouls[f"comment_{i}"].noul
        if score > NOISE_THRESHOLD:
            kept_comments.append(c)
        else:
            # Full comment plus task name, so a wrong drop can be found and judged.
            print(f"dropped (noul={score:.2f}) [{c['task']}]: {' '.join(c['comment'].split())}")

    return kept_tasks, kept_comments


def format_filtered(tasks: list[dict], comments: list[dict], dropped_count: int = 0) -> str:
    if not tasks and not comments:
        if dropped_count > 0:
            return f"_No notable Asana activity today_ ({_filtered_note(dropped_count)})"
        return "_No notable Asana activity today_"

    lines = []
    for t in tasks:
        project = project_name(t)
        prefix = f"**{project}** — " if project else ""
        lines.append(f"- {prefix}{t['name']}: marked complete.")
    for c in comments:
        project = c.get("project")
        prefix = f"**{project}** — " if project else ""
        lines.append(f"- {prefix}{c['task']}: {_clean_comment(c['comment'])}")
    if dropped_count > 0:
        lines.append(f"_({_filtered_note(dropped_count)})_")
    return "\n".join(lines)


def _filtered_note(count: int) -> str:
    return f"{count} item{'' if count == 1 else 's'} filtered as noise"


def _clean_comment(text: str, max_len: int = 200) -> str:
    """Collapse whitespace/newlines and truncate — raw Asana story text can
    span paragraphs or contain its own '- ' list, which would corrupt the
    log's bullet structure and confuse classify_log.py's section parser."""
    # Strip leading list markers from each line before collapsing whitespace.
    # This prevents markdown list syntax from corrupting the bullet structure
    # while preserving legitimate mid-sentence hyphens.
    lines = text.splitlines()
    cleaned_lines = [re.sub(r'^\s*-\s+', '', line) for line in lines]
    collapsed = " ".join(" ".join(cleaned_lines).split())
    if len(collapsed) > max_len:
        return collapsed[:max_len].rstrip() + "…"
    return collapsed
