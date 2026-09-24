from log_writer import read_section


def extract_bullets(log_text: str) -> list[str]:
    """Pull every non-empty '- ' line from the Done section."""
    section = read_section(log_text, "Done")
    bullets = []
    for line in section.splitlines():
        line = line.strip()
        if line.startswith("- ") and line[2:].strip():
            bullets.append(line[2:].strip())
    return bullets
