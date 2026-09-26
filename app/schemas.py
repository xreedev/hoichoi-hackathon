"""Data contracts shared by every stage (spec §4). Stages read/write these as JSON."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

SpeechSource = Literal["silero", "sarvam", "gemini_transcribe"]
Mood = Literal["happy", "neutral", "tense", "sad", "romantic", "comic", "angry"]
CandidateStatus = Literal["selected", "rejected", "pending"]


class VideoMeta(BaseModel):
    sha256: str
    src_path: str
    duration_s: float
    fps: float
    width: int
    height: int
    proxy_path: str
    audio_path: str


class Shot(BaseModel):
    id: int
    start_s: float
    end_s: float


class SpeechSegment(BaseModel):
    start_s: float
    end_s: float
    source: SpeechSource
    speaker: str | None = None


class TranscriptChunk(BaseModel):
    start_s: float
    end_s: float
    speaker: str | None = None
    text: str
    source: SpeechSource


class SensitiveTagEnum(StrEnum):
    funeral = "funeral"
    death = "death"
    grief = "grief"
    illness_or_hospital = "illness_or_hospital"
    violence_or_blood = "violence_or_blood"
    accident = "accident"
    crime_or_police = "crime_or_police"
    alcohol_or_drugs = "alcohol_or_drugs"
    religious_ritual = "religious_ritual"
    sexual_or_intimate = "sexual_or_intimate"
    child_in_distress = "child_in_distress"
    none = "none"


class SensitiveTag(BaseModel):
    tag: SensitiveTagEnum
    confidence: float = Field(ge=0.0, le=1.0)


class Scene(BaseModel):
    id: int
    start_s: float
    end_s: float
    shot_ids: list[int]
    summary_en: str
    dominant_activity: str
    setting: str
    mood: Mood
    narrative_tension: float = Field(ge=0.0, le=1.0)
    sensitive_tags: list[SensitiveTag]
    ends_on_cliffhanger: bool


class Candidate(BaseModel):
    id: int
    t_s: float
    shot_boundary_id: int
    pause_before_s: float
    pause_after_s: float
    in_speech: bool
    in_transcript_chunk: bool
    prev_scene_id: int | None
    next_scene_id: int | None
    is_scene_boundary: bool
    visual_change: float | None = None
    decisions: dict[str, Any] = Field(default_factory=dict)
    escalated: bool = False
    score: float | None = None
    status: CandidateStatus = "pending"
    reasons: list[str] = Field(default_factory=list)


class Brand(BaseModel):
    id: str
    name: str
    category: str
    description: str
    target_contexts: list[str]
    negative_contexts: list[str]
    creative_path: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class BlockedBrand(BaseModel):
    brand_id: str
    reason: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class Placement(BaseModel):
    break_id: str
    t_s: float
    brand_id: str
    brand_probability: float
    brand_reason: str
    blocked_brands: list[BlockedBrand] = Field(default_factory=list)
    creative_path: str | None = None
    ad_duration_s: float


class RunResult(BaseModel):
    meta: VideoMeta
    config_snapshot: dict[str, Any]
    model_versions: dict[str, str] = Field(default_factory=dict)
    timings_s: dict[str, float] = Field(default_factory=dict)
    scenes: list[Scene] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)
    placements: list[Placement] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
