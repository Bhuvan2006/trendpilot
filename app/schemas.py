"""Contracts for agent output. Ranges are checked here, not in the JSON schema, because Claude structured outputs reject numeric bounds."""
from typing import Literal

from pydantic import BaseModel, field_validator

Pillar = Literal["Behavioural AI", "Living Twin", "Zero-Prompt AI", "Causal intelligence", "Privacy-first AI"]
Platform = Literal["linkedin", "instagram"]


class ScoutTopic(BaseModel):
    source_url: str
    topic: str
    summary: str


class ScoutOutput(BaseModel):
    topics: list[ScoutTopic]


class TrendScore(BaseModel):
    source_url: str
    pillar: Pillar
    relevance: int
    brand_fit: int
    evidence: int
    content_potential: int
    reason: str
    angle: str

    @field_validator("relevance", "brand_fit", "evidence", "content_potential")
    @classmethod
    def in_range(cls, v: int) -> int:
        if not 0 <= v <= 100:
            raise ValueError("score must be between 0 and 100")
        return v


class StrategyOutput(BaseModel):
    trends: list[TrendScore]


class DraftOut(BaseModel):
    platform: Platform
    body: str


class StudioOutput(BaseModel):
    drafts: list[DraftOut]


class ReviewOutput(BaseModel):
    approved: bool
    score: int
    unsupported_claims: list[str]
    suggested_edits: list[str]
    explanation: str


def score(relevance, brand_fit, momentum, evidence, content_potential) -> float:
    return round(0.30 * relevance + 0.25 * brand_fit + 0.20 * momentum + 0.15 * evidence + 0.10 * content_potential, 2)
