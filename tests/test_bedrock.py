"""Bedrock provider against a stubbed bedrock-runtime client (no credentials, no network)."""

import boto3
import pytest
from botocore.stub import Stubber

from foundry.config import load_config
from foundry.models.base import CompletionRequest, ProviderError
from foundry.models.bedrock import BedrockProvider
from foundry.models.registry import build_provider


@pytest.fixture(autouse=True)
def fake_creds(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")


def _provider():
    client = boto3.client("bedrock-runtime", region_name="us-west-2")
    p = BedrockProvider("us.amazon.nova-2-lite-v1:0", region="us-west-2", max_tokens=2048, timeout_s=120, retries=2,
                        client=client)
    return p, Stubber(client)


REQ = CompletionRequest(system="sys", user="score this", max_tokens=2048, purpose="judge")


def test_complete_maps_text_and_usage():
    p, stub = _provider()
    stub.add_response("converse", {
        "output": {"message": {"role": "assistant", "content": [{"text": '{"criteria": []}'}]}},
        "stopReason": "end_turn", "usage": {"inputTokens": 900, "outputTokens": 40, "totalTokens": 940},
        "metrics": {"latencyMs": 10}},
        {"modelId": "us.amazon.nova-2-lite-v1:0", "messages": [{"role": "user", "content": [{"text": "score this"}]}],
         "system": [{"text": "sys"}], "inferenceConfig": {"maxTokens": 2048}})
    with stub:
        c = p.complete(REQ)
    assert (c.text, c.tokens_in, c.tokens_out, c.model_id) == ('{"criteria": []}', 900, 40, "us.amazon.nova-2-lite-v1:0")


def test_throttling_becomes_provider_error():
    p, stub = _provider()
    stub.add_client_error("converse", service_error_code="ThrottlingException", http_status_code=429)
    with stub, pytest.raises(ProviderError, match="ThrottlingException"):
        p.complete(REQ)


def test_role_settings_and_strands_model():
    cfg = load_config("config/bedrock.toml")
    judge = build_provider(cfg.judge)
    assert judge.boto_config.read_timeout == 120 and judge.boto_config.retries["max_attempts"] == 3
    agent = build_provider(cfg.model, prompt_id="adb-001", max_tokens=cfg.limits.max_tokens,
                           timeout_s=cfg.limits.model_timeout_s, retries=cfg.limits.model_retries)
    model = agent.strands_model()
    mc = model.get_config()
    assert mc["model_id"] == "us.anthropic.claude-sonnet-4-6" and mc["max_tokens"] == 4096 and mc["temperature"] == 0.2


def test_bedrock_configs_differ_only_in_model_id():
    a = open("config/bedrock.toml").read().splitlines()
    b = open("config/bedrock-alt.toml").read().splitlines()
    diff = [(x, y) for x, y in zip(a, b, strict=True) if x != y]
    assert diff == [('id = "us.anthropic.claude-sonnet-4-6"', 'id = "us.anthropic.claude-haiku-4-5-20251001-v1:0"')]


def test_kimi_config_omits_temperature():
    """Kimi K3 rejects inferenceConfig.temperature (live, 2026-10-30); its config must not set one."""
    cfg = load_config("config/bedrock-kimi.toml")
    assert cfg.model.id == "us.moonshotai.kimi-k3" and cfg.model.temperature is None
    agent = build_provider(cfg.model, prompt_id="adb-001", max_tokens=cfg.limits.max_tokens,
                           timeout_s=cfg.limits.model_timeout_s, retries=cfg.limits.model_retries)
    assert "temperature" not in agent.strands_model().get_config()


def test_complete_counts_cached_input_tokens():
    p, stub = _provider()
    stub.add_response("converse", {
        "output": {"message": {"role": "assistant", "content": [{"text": "x"}]}}, "stopReason": "end_turn",
        "usage": {"inputTokens": 7, "outputTokens": 50, "totalTokens": 4140, "cacheReadInputTokens": 4083,
                  "cacheWriteInputTokens": 0},
        "metrics": {"latencyMs": 10}})
    with stub:
        assert p.complete(REQ).tokens_in == 4090
