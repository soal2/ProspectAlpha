"""Configuration loading for external model providers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from riskagent.env import load_dotenv


ProviderKind = Literal["embedding", "vllm", "llm"]

DEFAULT_PROVIDER_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "providers.yaml"


class ProviderConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelProviderConfig:
    provider: str
    model: str
    api_key: str | None = None
    base_url: str | None = None
    options: dict[str, Any] | None = None


def load_model_provider_config(
    kind: ProviderKind,
    *,
    path: str | Path | None = None,
    selected_model_key: str | None = None,
) -> ModelProviderConfig:
    load_dotenv()
    config_path = Path(path or os.getenv("PROVIDER_CONFIG_PATH") or DEFAULT_PROVIDER_CONFIG_PATH)
    if not config_path.exists():
        raise ProviderConfigError(f"Provider config file does not exist: {config_path}")
    with config_path.open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or {}
    providers = raw.get("providers")
    if not isinstance(providers, dict):
        raise ProviderConfigError("Provider config must define a providers mapping.")
    kind_config = providers.get(kind)
    if not isinstance(kind_config, dict):
        raise ProviderConfigError(f"Provider config must define providers.{kind}.")
    models = kind_config.get("models")
    if not isinstance(models, dict) or not models:
        raise ProviderConfigError(f"Provider config must define providers.{kind}.models.")
    env_model_key = os.getenv(f"{kind.upper()}_MODEL_KEY")
    model_key = selected_model_key or env_model_key or kind_config.get("default")
    if not isinstance(model_key, str) or not model_key.strip():
        raise ProviderConfigError(f"Provider config must define providers.{kind}.default.")
    model_config = models.get(model_key)
    if not isinstance(model_config, dict):
        raise ProviderConfigError(f"Provider model is not configured: providers.{kind}.models.{model_key}")
    provider = _required_string(model_config, "provider", f"providers.{kind}.models.{model_key}.provider")
    model = _required_string(model_config, "model", f"providers.{kind}.models.{model_key}.model")
    api_key = _resolve_value(model_config, "api_key", "api_key_env")
    base_url = _resolve_value(model_config, "base_url", "base_url_env")
    options = model_config.get("options")
    if options is not None and not isinstance(options, dict):
        raise ProviderConfigError(f"providers.{kind}.models.{model_key}.options must be a mapping.")
    return ModelProviderConfig(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        options=options,
    )


def _required_string(config: dict[str, Any], field: str, path: str) -> str:
    value = config.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ProviderConfigError(f"Provider config value is required: {path}")
    return value.strip()


def _resolve_value(config: dict[str, Any], value_field: str, env_field: str) -> str | None:
    value = config.get(value_field)
    env_name = config.get(env_field)
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(env_name, str) and env_name.strip():
        env_value = os.getenv(env_name.strip())
        return env_value.strip() if env_value else None
    return None
