from log_writer import read_section
from typesafe_sdk import Choice, TypeSafeClient


def extract_bullets(log_text: str) -> list[str]:
    """Pull every non-empty '- ' line from the Done section."""
    section = read_section(log_text, "Done")
    bullets = []
    for line in section.splitlines():
        line = line.strip()
        if line.startswith("- ") and line[2:].strip():
            bullets.append(line[2:].strip())
    return bullets


CATEGORY_CRITERIA = {
    "product_qa": (
        "Technical or product work: debugging with a named root cause, a shipped fix, "
        "a spec or design doc, documented UX/product feedback, a SQL/data finding, "
        "AI-prototyping or eval work, a decision made directly with product leadership, "
        "or a portfolio-worthy technical finding."
    ),
    "ops_transferable": (
        "Operations or account work that is formally documented, process-improving, or "
        "transferable: coaching or managing a report with a written structure, a "
        "documented policy/process decision, an account resolution with stated reasoning, "
        "delegation with tracked follow-through."
    ),
    "ops_noise": (
        "Routine account administration, inbox/channel monitoring, task routing with no "
        "decision attached, or a brief approval with no stated reasoning."
    ),
}


def classify_items(api_key: str, items: list[str]) -> list[dict]:
    """Classify each Done bullet via one Jev Choice call, one question per item."""
    if not items:
        return []

    questions = {
        f"item_{i}": Choice(
            instructions={"item": item, "question": "Which category best describes `item`?"},
            criteria=CATEGORY_CRITERIA,
        )
        for i, item in enumerate(items)
    }
    client = TypeSafeClient(api_key=api_key, model="jev-latest", timeout=60.0)
    result = client.system_one({}, questions)

    return [
        {
            "text": item,
            "category": result.choices[f"item_{i}"].choice,
            "confidence": result.choices[f"item_{i}"].confidence,
        }
        for i, item in enumerate(items)
    ]
