"""Secret-safe YAML configuration for the streaming model provider."""

import math
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import yaml


class ConfigurationError(RuntimeError):
    """Raised when the local LLM configuration cannot be used."""


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    base_url: str
    api_key: str = field(repr=False)
    model: str
    timeout_seconds: float
    temperature: float | None = None
    max_tokens: int | None = None
    max_turns: int = 10
    tool_timeout_seconds: float = 1800.0


_FIELDS = {
    "provider",
    "base_url",
    "api_key",
    "model",
    "timeout_seconds",
    "temperature",
    "max_tokens",
    "max_turns",
    "tool_timeout_seconds",
}

_REQUIRED_FIELDS = {
    "provider",
    "base_url",
    "api_key",
    "model",
    "timeout_seconds",
}


def _required_string(values: dict[object, object], name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"llm.{name} must be a non-empty string")
    return value.strip()


def load_llm_config(path: str | Path) -> LLMConfig:
    config_path = Path(path)
    try:
        source = config_path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ConfigurationError(f"LLM config file not found: {config_path}") from error
    except OSError as error:
        raise ConfigurationError(f"Unable to read LLM config file: {config_path}") from error

    try:
        document = yaml.safe_load(source)
    except yaml.YAMLError:
        raise ConfigurationError("LLM config must contain valid YAML") from None

    if not isinstance(document, dict):
        raise ConfigurationError("LLM config root must be a mapping")
    if "llm" not in document:
        raise ConfigurationError("LLM config section llm is required")
    if set(document) != {"llm"}:
        unknown = sorted(str(key) for key in set(document) - {"llm"})
        raise ConfigurationError(f"Unknown root config keys: {', '.join(unknown)}")

    values = document["llm"]
    if not isinstance(values, dict):
        raise ConfigurationError("LLM config section llm must be a mapping")

    keys = set(values)
    unknown_fields = sorted(str(key) for key in keys - _FIELDS)
    if unknown_fields:
        raise ConfigurationError(f"Unknown llm config keys: {', '.join(unknown_fields)}")
    missing_fields = sorted(_REQUIRED_FIELDS - keys)
    if missing_fields:
        raise ConfigurationError(
            f"Missing llm config keys: {', '.join(missing_fields)}"
        )

    provider = _required_string(values, "provider")
    if provider != "openai-compatible":
        raise ConfigurationError("llm.provider must be openai-compatible")

    base_url = _required_string(values, "base_url").rstrip("/")
    try:
        parsed_url = urlsplit(base_url)
        hostname = parsed_url.hostname
        parsed_url.port
    except ValueError:
        raise ConfigurationError("llm.base_url must be an HTTP(S) URL") from None
    if (
        parsed_url.scheme not in {"http", "https"}
        or not parsed_url.netloc
        or not hostname
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise ConfigurationError("llm.base_url must be an HTTP(S) URL")

    timeout_value = values.get("timeout_seconds")
    if (
        isinstance(timeout_value, bool)
        or not isinstance(timeout_value, (int, float))
        or timeout_value <= 0
        or not math.isfinite(timeout_value)
    ):
        raise ConfigurationError("llm.timeout_seconds must be a positive number")

    temperature_value = values.get("temperature")
    if temperature_value is not None and (
        isinstance(temperature_value, bool)
        or not isinstance(temperature_value, (int, float))
        or not math.isfinite(temperature_value)
        or temperature_value < 0
        or temperature_value > 2
    ):
        raise ConfigurationError("llm.temperature must be between 0 and 2")

    max_tokens_value = values.get("max_tokens")
    if max_tokens_value is not None and (
        isinstance(max_tokens_value, bool)
        or not isinstance(max_tokens_value, int)
        or max_tokens_value <= 0
    ):
        raise ConfigurationError("llm.max_tokens must be a positive integer")

    max_turns_value = values.get("max_turns", 10)
    if (
        isinstance(max_turns_value, bool)
        or not isinstance(max_turns_value, int)
        or max_turns_value < 1
    ):
        raise ConfigurationError("llm.max_turns must be a positive integer")

    tool_timeout_value = values.get("tool_timeout_seconds", 1800)
    if (
        isinstance(tool_timeout_value, bool)
        or not isinstance(tool_timeout_value, (int, float))
        or tool_timeout_value <= 0
        or not math.isfinite(tool_timeout_value)
    ):
        raise ConfigurationError(
            "llm.tool_timeout_seconds must be a positive number"
        )

    return LLMConfig(
        provider=provider,
        base_url=base_url,
        api_key=_required_string(values, "api_key"),
        model=_required_string(values, "model"),
        timeout_seconds=float(timeout_value),
        temperature=(
            None if temperature_value is None else float(temperature_value)
        ),
        max_tokens=(
            None
            if max_tokens_value is None
            else max(32768, max_tokens_value)
        ),
        max_turns=max_turns_value,
        tool_timeout_seconds=float(tool_timeout_value),
    )
