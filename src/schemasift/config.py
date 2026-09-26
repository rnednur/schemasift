from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .exceptions import ConfigurationError
from .providers import LexicalProvider, OpenAICompatibleProvider, ReplayProvider, SystemOneProvider
from .providers.base import DecisionProvider
from .selector import SchemaSelector
from .tracing import JsonlTraceSink, NullTraceSink


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuthenticationConfig(ConfigModel):
    type: Literal["none", "bearer", "header"] = "none"
    token_env: str | None = None
    header_name: str = "Authorization"
    scheme: str = "Bearer"

    @model_validator(mode="after")
    def token_required(self):
        if self.type != "none" and not self.token_env:
            raise ValueError("token_env is required for authenticated providers")
        return self


class ProviderConfig(ConfigModel):
    adapter: Literal["system_one", "openrouter_decisions", "openai_compatible", "lexical", "replay"]
    base_url: str | None = None
    endpoint: str | None = None
    model: str
    authentication: AuthenticationConfig = Field(default_factory=AuthenticationConfig)
    timeout_ms: int = Field(default=10_000, ge=1)
    max_retries: int = Field(default=2, ge=0, le=10)
    input_cost_per_million: float | None = Field(default=None, ge=0)
    output_cost_per_million: float | None = Field(default=None, ge=0)
    reasoning_effort: str | None = None
    structured_output: bool = True
    replay_file: str | None = None


class TracingConfig(ConfigModel):
    enabled: bool = True
    jsonl_path: str | None = "runs/schemasift-traces.jsonl"


class ServerConfig(ConfigModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)


class SchemaSiftConfig(ConfigModel):
    default_provider: str
    providers: dict[str, ProviderConfig]
    tracing: TracingConfig = Field(default_factory=TracingConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)

    @model_validator(mode="after")
    def default_exists(self):
        if self.default_provider not in self.providers:
            raise ValueError("default_provider is not present in providers")
        return self


def load_config(path: str | Path) -> SchemaSiftConfig:
    try:
        data = yaml.safe_load(Path(path).read_text())
        return SchemaSiftConfig.model_validate(data)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"unable to load configuration {path}: {exc}") from exc


def build_selector(config: SchemaSiftConfig) -> SchemaSelector:
    providers: dict[str, DecisionProvider] = {}
    for name, provider_config in config.providers.items():
        if provider_config.adapter == "lexical":
            provider = LexicalProvider(name=name, model=provider_config.model)
        elif provider_config.adapter == "replay":
            if not provider_config.replay_file:
                raise ConfigurationError(f"replay provider {name} requires replay_file")
            provider = ReplayProvider(provider_config.replay_file, name=name,
                                      model=provider_config.model)
        else:
            if not provider_config.base_url:
                raise ConfigurationError(f"provider {name} requires base_url")
            authentication = provider_config.authentication
            api_key = os.environ.get(authentication.token_env or "") or None
            endpoint = provider_config.endpoint or {
                "openrouter_decisions": "/api/alpha/decisions",
                "openai_compatible": "/chat/completions",
                "system_one": "/v1/systemone",
            }[provider_config.adapter]
            provider_class = (OpenAICompatibleProvider
                              if provider_config.adapter == "openai_compatible" else SystemOneProvider)
            provider = provider_class(
                name=name, base_url=provider_config.base_url, endpoint=endpoint,
                model=provider_config.model, api_key=api_key,
                auth_header=authentication.header_name,
                auth_scheme=authentication.scheme if authentication.type != "none" else "",
                timeout_ms=provider_config.timeout_ms, max_retries=provider_config.max_retries,
                input_cost_per_million=provider_config.input_cost_per_million,
                **({"output_cost_per_million": provider_config.output_cost_per_million,
                    "reasoning_effort": provider_config.reasoning_effort,
                    "structured_output": provider_config.structured_output}
                   if provider_config.adapter == "openai_compatible" else {}),
            )
        providers[name] = provider
    trace_sink = (JsonlTraceSink(config.tracing.jsonl_path)
                  if config.tracing.enabled and config.tracing.jsonl_path else NullTraceSink())
    return SchemaSelector(providers, default_provider=config.default_provider,
                          trace_sink=trace_sink)


def load_selector(path: str | Path) -> SchemaSelector:
    """Public convenience API for in-process, configuration-driven use."""
    return build_selector(load_config(path))
