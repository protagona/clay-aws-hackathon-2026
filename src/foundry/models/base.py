"""The model seam: every model role (agent, judge, summarizer) is a Provider chosen by config."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from strands.models import Model

    from foundry.contract import Rubric


@dataclass
class CompletionRequest:
    system: str
    user: str
    max_tokens: int
    purpose: Literal["judge", "summary"]
    rubric: Rubric | None = None


@dataclass
class Completion:
    text: str
    tokens_in: int
    tokens_out: int
    model_id: str


class ProviderError(Exception):
    """A model call failed after the provider's own retries."""


class Provider(Protocol):
    provider: str
    model_id: str

    def strands_model(self) -> Model: ...

    def complete(self, req: CompletionRequest) -> Completion: ...
