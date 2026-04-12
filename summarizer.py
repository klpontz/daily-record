from pathlib import Path
import anthropic


def summarize(api_key: str, prompt_path: str, data: str) -> str:
    """Summarize raw data using Claude API.

    Loads the prompt from a file, combines it with the raw data, and
    returns Claude's response as a string.

    Args:
        api_key: Anthropic API key
        prompt_path: Path to the prompt markdown file
        data: Raw data string to summarize
    """
    prompt = Path(prompt_path).read_text()
    client = anthropic.Anthropic(api_key=api_key)

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": f"{prompt}\n\n---\n\n{data}"
            }
        ]
    )
    return message.content[0].text
