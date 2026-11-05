"""Secret-safe, uncached loading of local Markdown prompts."""

from pathlib import Path


MAX_PROMPT_BYTES = 1024 * 1024


class PromptLoadError(RuntimeError):
    """Raised when a local prompt file cannot be loaded safely."""


def load_prompt(path: str | Path) -> str:
    """Read and validate one UTF-8 prompt file on every invocation."""

    prompt_path = Path(path)
    try:
        raw = prompt_path.read_bytes()
    except FileNotFoundError as error:
        raise PromptLoadError(f"Prompt file not found: {prompt_path}") from error
    except OSError as error:
        raise PromptLoadError(f"Unable to read prompt file: {prompt_path}") from error

    if len(raw) > MAX_PROMPT_BYTES:
        raise PromptLoadError("Prompt file exceeds 1 MiB")
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PromptLoadError("Prompt file must be valid UTF-8") from error
    if not content.strip():
        raise PromptLoadError("Prompt file must not be empty")
    return content
