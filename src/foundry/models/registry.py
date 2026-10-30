"""Build the Provider for a model role from its config section."""

from __future__ import annotations

from foundry.config import ModelCfg, RoleCfg, resolve
from foundry.models.base import Provider


def build_provider(cfg: ModelCfg, *, prompt_id: str | None = None, max_tokens: int = 1024,
                   timeout_s: float = 120, retries: int = 2) -> Provider:
    if isinstance(cfg, RoleCfg):
        max_tokens, timeout_s, retries = cfg.max_tokens, cfg.timeout_s, cfg.retries
    if cfg.provider == "scripted":
        from foundry.models.scripted import ScriptedProvider

        script = cfg.script.format(prompt_id=prompt_id or "")
        return ScriptedProvider(cfg.id, resolve(script))
    if cfg.provider == "offline":
        from foundry.models.offline import OfflineProvider

        return OfflineProvider(cfg.id)
    if cfg.provider == "bedrock":
        from foundry.models.bedrock import BedrockProvider

        return BedrockProvider(cfg.id, region=cfg.region, max_tokens=max_tokens, timeout_s=timeout_s,
                               retries=retries, temperature=cfg.temperature)
    raise ValueError(f"unknown provider {cfg.provider!r}")
