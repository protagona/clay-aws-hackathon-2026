"""Amazon Bedrock: Strands' BedrockModel for the agent, the Converse API for the judge and summarizer."""

from __future__ import annotations

from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from foundry.models.base import Completion, CompletionRequest, ProviderError


class BedrockProvider:
    provider = "bedrock"

    def __init__(self, model_id: str, *, region: str, max_tokens: int, timeout_s: float, retries: int,
                 temperature: float | None = None, client=None):
        self.model_id = model_id
        self.region = region
        self.max_tokens = max_tokens
        self.temperature = temperature
        # Transport retries live here (botocore, exponential backoff); Strands' own retry loop is disabled.
        self.boto_config = BotoConfig(read_timeout=timeout_s, connect_timeout=10,
                                      retries={"max_attempts": retries + 1, "mode": "standard"})
        self._client = client

    def strands_model(self):
        from strands.models import BedrockModel

        params = {"max_tokens": self.max_tokens}
        if self.temperature is not None:
            params["temperature"] = self.temperature
        return BedrockModel(model_id=self.model_id, region_name=self.region, boto_client_config=self.boto_config,
                            **params)

    @property
    def client(self):
        if self._client is None:
            import boto3

            self._client = boto3.client("bedrock-runtime", region_name=self.region, config=self.boto_config)
        return self._client

    def complete(self, req: CompletionRequest) -> Completion:
        inference = {"maxTokens": min(req.max_tokens, self.max_tokens)}
        if self.temperature is not None:
            inference["temperature"] = self.temperature
        kwargs = {"modelId": self.model_id, "messages": [{"role": "user", "content": [{"text": req.user}]}],
                  "inferenceConfig": inference}
        if req.system:
            kwargs["system"] = [{"text": req.system}]
        try:
            resp = self.client.converse(**kwargs)
        except (ClientError, BotoCoreError) as exc:
            raise ProviderError(f"Bedrock {self.model_id}: {exc}") from None
        content = resp.get("output", {}).get("message", {}).get("content", [])
        text = "".join(b.get("text", "") for b in content)
        usage = resp.get("usage", {})
        from foundry.harness.hooks import total_input_tokens

        return Completion(text, total_input_tokens(usage) or 0, usage.get("outputTokens", 0), self.model_id)
