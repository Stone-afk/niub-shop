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
    prompt_file: Path
    recommendation_policy_prompt_file: Path


_FIELDS = {
    "provider",
    "base_url",
    "api_key",
    "model",
    "timeout_seconds",
    "prompt_file",
    "recommendation_policy_prompt_file",
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
    missing_fields = sorted(_FIELDS - keys)
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

    prompt_file = config_path.parent / _required_string(values, "prompt_file")
    recommendation_policy_prompt_file = config_path.parent / _required_string(
        values, "recommendation_policy_prompt_file"
    )

    return LLMConfig(
        provider=provider,
        base_url=base_url,
        api_key=_required_string(values, "api_key"),
        model=_required_string(values, "model"),
        timeout_seconds=float(timeout_value),
        prompt_file=prompt_file,
        recommendation_policy_prompt_file=recommendation_policy_prompt_file,
    )
