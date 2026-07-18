"""Research configuration.

Secrets use ``pydantic.SecretStr`` so the API key never appears in
``repr``/``str``/logs/exceptions/event payloads.  Real keys must never be
committed; they are supplied via environment variables only.
"""

from __future__ import annotations

import os
from typing import Optional

from pydantic import BaseModel, Field, SecretStr

from .models import ExternalSearchPolicy

DEFAULT_BOCHA_ENDPOINT = "https://api.bochaai.com/v1/web-search"


class BochaConfig(BaseModel):
    api_key: Optional[SecretStr] = None
    endpoint: str = DEFAULT_BOCHA_ENDPOINT
    timeout_seconds: float = 10.0
    default_count: int = 10

    @classmethod
    def from_env(cls) -> "BochaConfig":
        key = os.getenv("BOCHA_API_KEY") or None
        return cls(
            api_key=SecretStr(key) if key else None,
            endpoint=os.getenv("BOCHA_ENDPOINT") or DEFAULT_BOCHA_ENDPOINT,
            timeout_seconds=float(os.getenv("BOCHA_TIMEOUT") or 10.0),
            default_count=int(os.getenv("BOCHA_DEFAULT_COUNT") or 10),
        )


class ResearchConfig(BaseModel):
    policy: ExternalSearchPolicy = ExternalSearchPolicy.public_only
    bocha: BochaConfig = Field(default_factory=BochaConfig)
    cache_dir: Optional[str] = None
    cache_ttl_seconds: int = 7 * 24 * 3600
    max_retries: int = 2
    retry_backoff_seconds: float = 0.5
    extract_max_chars: int = 8000
    fetch_max_bytes: int = 2 * 1024 * 1024
    fetch_max_redirects: int = 3
    fetch_total_seconds: float = 15.0

    @classmethod
    def from_env(cls) -> "ResearchConfig":
        policy_raw = os.getenv("RESEARCH_SEARCH_POLICY") or ExternalSearchPolicy.public_only.value
        try:
            policy = ExternalSearchPolicy(policy_raw)
        except ValueError:
            policy = ExternalSearchPolicy.public_only
        return cls(
            policy=policy,
            bocha=BochaConfig.from_env(),
            cache_dir=os.getenv("RESEARCH_CACHE_DIR") or None,
        )
