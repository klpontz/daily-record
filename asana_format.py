import re

from asana_client import project_name


def format_items(tasks: list[dict], comments: list[dict]) -> str:
    """Write every Asana task and comment as one bullet. Nothing is filtered.

    The daily review decides what matters. The Jev noise filter removed real
    work too often, so it was taken out on 2026-11-19.
    """
    if not tasks and not comments:
        return "_No notable Asana activity today_"

    lines = []
    for t in tasks:
        project = project_name(t)
        prefix = f"**{project}** — " if project else ""
        label = "created" if t.get("action") == "created" else "marked complete"
        lines.append(f"- {prefix}{t['name']}: {label}.")
    for c in comments:
        project = c.get("project")
        prefix = f"**{project}** — " if project else ""
        lines.append(f"- {prefix}{c['task']}: {_clean_comment(c['comment'])}")
    return "\n".join(lines)


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
