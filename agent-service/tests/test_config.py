import traceback
from pathlib import Path

import pytest

from agent_service.config import ConfigurationError, load_llm_config


SECRET = "test-secret-never-log"


def write_config(tmp_path: Path, body: str) -> Path:
    (tmp_path / "system.md").write_text("你是商城助手。", encoding="utf-8")
    (tmp_path / "recommendation-policy.md").write_text(
        "先搜索商品，再提交推荐。", encoding="utf-8"
    )
    path = tmp_path / "llm.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def valid_yaml(**overrides: object) -> str:
    values: dict[str, object] = {
        "provider": "openai-compatible",
        "base_url": "https://api.example.test/v1/",
        "api_key": SECRET,
        "model": "example-chat",
        "timeout_seconds": 45,
        "prompt_file": "system.md",
        "recommendation_policy_prompt_file": "recommendation-policy.md",
    }
    values.update(overrides)
    lines = ["llm:"]
    for key, value in values.items():
        if isinstance(value, str):
            lines.append(f"  {key}: {value!r}")
        else:
            lines.append(f"  {key}: {value}")
    return "\n".join(lines) + "\n"


def test_loads_complete_config_and_hides_api_key_from_repr(tmp_path: Path) -> None:
    config = load_llm_config(write_config(tmp_path, valid_yaml()))

    assert config.provider == "openai-compatible"
    assert config.base_url == "https://api.example.test/v1"
    assert config.api_key == SECRET
    assert config.model == "example-chat"
    assert config.timeout_seconds == 45.0
    assert config.prompt_file == tmp_path / "system.md"
    assert config.recommendation_policy_prompt_file == (
        tmp_path / "recommendation-policy.md"
    )
    assert config.temperature is None
    assert config.max_tokens is None
    assert config.max_turns == 10
    assert config.tool_timeout_seconds == 1800.0
    assert SECRET not in repr(config)


def test_loads_generation_and_runtime_limits(tmp_path: Path) -> None:
    config = load_llm_config(
        write_config(
            tmp_path,
            valid_yaml(
                temperature=0.7,
                max_tokens=800,
                timeout_seconds=1800,
                max_turns=10,
                tool_timeout_seconds=1800,
            ),
        )
    )

    assert config.temperature == 0.7
    assert config.max_tokens == 32768
    assert config.timeout_seconds == 1800.0
    assert config.max_turns == 10
    assert config.tool_timeout_seconds == 1800.0


def test_missing_config_file_has_safe_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not found") as caught:
        load_llm_config(tmp_path / "missing.yaml")

    assert SECRET not in str(caught.value)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("llm: [", "valid YAML"),
        ("- item\n", "root must be a mapping"),
        ("llm: value\n", "llm must be a mapping"),
        ("other: {}\n", "llm is required"),
    ],
)
def test_rejects_invalid_yaml_shapes(
    tmp_path: Path,
    body: str,
    message: str,
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        load_llm_config(write_config(tmp_path, body))


def test_malformed_yaml_traceback_does_not_expose_source_secret(
    tmp_path: Path,
) -> None:
    exposed_secret = "synthetic-secret-must-stay-private"
    body = f"llm:\n  api_key: [{exposed_secret}\n"

    with pytest.raises(ConfigurationError) as caught:
        load_llm_config(write_config(tmp_path, body))

    rendered = "".join(
        traceback.format_exception(
            caught.type,
            caught.value,
            caught.tb,
        )
    )
    assert exposed_secret not in rendered


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", "''"),
        ("base_url", "''"),
        ("api_key", "''"),
        ("model", "''"),
        ("prompt_file", "''"),
        ("recommendation_policy_prompt_file", "''"),
        ("provider", "123"),
        ("base_url", "[]"),
        ("api_key", "false"),
        ("model", "{}"),
        ("prompt_file", "9"),
        ("recommendation_policy_prompt_file", "9"),
    ],
)
def test_rejects_empty_or_wrong_type_string_fields(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    body = valid_yaml().replace(
        next(line for line in valid_yaml().splitlines() if line.strip().startswith(f"{field}:")),
        f"  {field}: {value}",
    )

    with pytest.raises(ConfigurationError, match=field) as caught:
        load_llm_config(write_config(tmp_path, body))

    assert SECRET not in str(caught.value)


def test_rejects_missing_required_field_without_echoing_secret(tmp_path: Path) -> None:
    body = valid_yaml().replace("  model: 'example-chat'\n", "")

    with pytest.raises(ConfigurationError, match="model") as caught:
        load_llm_config(write_config(tmp_path, body))

    assert SECRET not in str(caught.value)


def test_rejects_unsupported_provider(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="provider"):
        load_llm_config(
            write_config(tmp_path, valid_yaml(provider="vendor-specific"))
        )


@pytest.mark.parametrize(
    "base_url",
    [
        "ftp://example.test",
        "example.test",
        "/api",
        "https://example.test:not-a-port",
        "https://[invalid-ipv6",
    ],
)
def test_rejects_non_http_base_url(tmp_path: Path, base_url: str) -> None:
    with pytest.raises(ConfigurationError, match="base_url"):
        load_llm_config(write_config(tmp_path, valid_yaml(base_url=base_url)))


@pytest.mark.parametrize("timeout", [0, -1, "fast", True])
def test_rejects_invalid_timeout(tmp_path: Path, timeout: object) -> None:
    with pytest.raises(ConfigurationError, match="timeout_seconds"):
        load_llm_config(
            write_config(tmp_path, valid_yaml(timeout_seconds=timeout))
        )


@pytest.mark.parametrize("yaml_timeout", [".nan", ".inf", "-.inf"])
def test_rejects_non_finite_yaml_timeout(
    tmp_path: Path,
    yaml_timeout: str,
) -> None:
    body = valid_yaml().replace(
        "  timeout_seconds: 45",
        f"  timeout_seconds: {yaml_timeout}",
    )

    with pytest.raises(ConfigurationError, match="timeout_seconds"):
        load_llm_config(write_config(tmp_path, body))


def test_rejects_unknown_keys_without_echoing_secret(tmp_path: Path) -> None:
    body = valid_yaml() + "  unexpected: value\n"

    with pytest.raises(ConfigurationError, match="unexpected") as caught:
        load_llm_config(write_config(tmp_path, body))

    assert SECRET not in str(caught.value)
