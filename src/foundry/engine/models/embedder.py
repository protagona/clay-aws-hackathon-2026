"""Text embedders for the retrieval output scorer (requirement-gaps slice C): Bedrock Titan Text Embeddings V2 live,
a deterministic hashed bag of words offline (ADR-5). Every vector is unit length."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field

from foundry.config import EmbedderCfg

_TOKEN = re.compile(r"[a-z0-9]+")


class EmbedderError(Exception):
    pass


@dataclass
class Embedder:
    cfg: EmbedderCfg
    calls: list[int] = field(default_factory=list)  # input tokens per call (bedrock); [] for hash

    def __post_init__(self) -> None:  # subclasses set up clients here (the base dataclass __init__ calls it)
        pass

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v


class HashEmbedder(Embedder):
    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            v = [0.0] * self.cfg.dimensions
            for tok in _TOKEN.findall(text.lower()):
                tok = tok[:-1] if len(tok) > 3 and tok.endswith("s") else tok
                v[int(hashlib.sha1(tok.encode()).hexdigest(), 16) % self.cfg.dimensions] += 1.0
            out.append(_unit(v))
        return out


class BedrockEmbedder(Embedder):
    def __post_init__(self) -> None:
        import boto3
        from botocore.config import Config

        self._client = boto3.client("bedrock-runtime", region_name=self.cfg.region, config=Config(
            connect_timeout=5, read_timeout=self.cfg.timeout_s,
            retries={"mode": "standard", "total_max_attempts": 1 + self.cfg.retries}))

    def embed(self, texts: list[str]) -> list[list[float]]:
        start, out = time.monotonic(), []
        for i, text in enumerate(texts):
            if time.monotonic() - start > self.cfg.budget_s:
                raise EmbedderError(f"budget of {self.cfg.budget_s:g}s exhausted after {i} of {len(texts)} texts")
            body = json.dumps({"inputText": text, "dimensions": self.cfg.dimensions, "normalize": True})
            r = json.loads(self._client.invoke_model(modelId=self.cfg.id, body=body)["body"].read())
            vec = r.get("embedding")
            if not isinstance(vec, list) or len(vec) != self.cfg.dimensions:
                raise EmbedderError(f"unexpected response: embedding of length {len(vec or [])}")
            self.calls.append(int(r.get("inputTextTokenCount") or 0))
            out.append(_unit([float(x) for x in vec]))
        return out


def build_embedder(cfg: EmbedderCfg) -> Embedder:
    return HashEmbedder(cfg) if cfg.provider == "hash" else BedrockEmbedder(cfg)


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))
