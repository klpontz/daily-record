import re
from pathlib import Path
from datetime import date


def get_log_path(log_dir: str, target_date: date = None) -> Path:
    if target_date is None:
        target_date = date.today()
    return Path(log_dir) / f"{target_date.isoformat()}.md"


def overwrite_section(text: str, section_name: str, new_content: str) -> str:
    """Replace content under a ### section header, preserving all other sections.

    Finds '### section_name' and replaces everything under it until the next
    '### ' header or end of file. If section not found, appends it.
    """
    pattern = rf'(### {re.escape(section_name)}\n)(.*?)(?=\n### |\Z)'
    if re.search(pattern, text, flags=re.DOTALL):
        replacement = f'### {section_name}\n{new_content}\n'
        return re.sub(pattern, replacement, text, flags=re.DOTALL)
    # Section not found — append it
    return text.rstrip('\n') + f'\n\n### {section_name}\n{new_content}\n'


def write_log(log_path: Path, template_path: str, sections: dict) -> None:
    """Write sections to log file.

    If the file exists, overwrites the named sections and leaves all others untouched.
    If the file does not exist, creates it from the template then writes sections.

    Args:
        log_path: Path to the daily log file (e.g. .../2026-04-10.md)
        template_path: Path to daily-notes-template.md
        sections: dict mapping section_name -> content string
    """
    if log_path.exists():
        text = log_path.read_text()
    else:
        text = Path(template_path).read_text()

    for section_name, content in sections.items():
        text = overwrite_section(text, section_name, content)

    log_path.write_text(text)
