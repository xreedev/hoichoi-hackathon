"""Typed loader for config.yaml (spec §5). No magic numbers live in code; they live here."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"

log = logging.getLogger(__name__)


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelsCfg(_Section):
    scene_video: str
    text: str
    asr_fallback: str
    sarvam_asr: str
    jev: str
    vjepa: str


class FlagsCfg(_Section):
    use_sarvam: bool
    use_jev: bool
    use_vjepa: bool


class IngestCfg(_Section):
    proxy_height: int
    audio_sr: int


class ShotsCfg(_Section):
    detector: Literal["adaptive"]


class VadCfg(_Section):
    threshold: float
    min_silence_duration_ms: int
    min_speech_duration_ms: int


class CandidatesCfg(_Section):
    min_pause_s: float
    clearance_s: float
    edge_exclusion_s: float
    edge_exclusion_frac: float
    scene_boundary_tol_s: float


class DecideCfg(_Section):
    cascade_confidence_below: float


class GatesCfg(_Section):
    sensitive_block_p: float
    emotional_peak_reject_p: float
    cliffhanger_reject: bool
    negative_context_noul_block_p: float


class ScoringWeightsCfg(_Section):
    pause_len: float
    is_scene_boundary: float
    ends_scene_p: float
    break_quality: float
    low_tension_prev: float
    visual_change: float


class ScoringCfg(_Section):
    pause_len_cap_s: float


class PacingCfg(_Section):
    max_breaks_per_hour: int
    min_gap_s: float
    max_ad_load_pct: float
    default_ad_duration_s: float


class ConcurrencyCfg(_Section):
    gemini_max_parallel: int
    jev_max_parallel: int
    retry_max_attempts: int
    retry_base_delay_s: float


class TimeoutsCfg(_Section):
    gemini_text_s: float
    gemini_video_s: float
    gemini_file_active_s: float
    sarvam_job_s: float
    sarvam_poll_s: float
    jev_s: float


class PathsCfg(_Section):
    runs_dir: str
    catalogue: str
    synonyms: str


class Config(_Section):
    models: ModelsCfg
    flags: FlagsCfg
    ingest: IngestCfg
    shots: ShotsCfg
    vad: VadCfg
    candidates: CandidatesCfg
    decide: DecideCfg
    gates: GatesCfg
    scoring_weights: ScoringWeightsCfg
    scoring: ScoringCfg
    pacing: PacingCfg
    concurrency: ConcurrencyCfg
    timeouts: TimeoutsCfg
    paths: PathsCfg

    def path(self, rel: str) -> Path:
        """Resolve a config path relative to the repo root."""
        p = Path(rel)
        return p if p.is_absolute() else ROOT / p

    @property
    def runs_dir(self) -> Path:
        return self.path(self.paths.runs_dir)


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_env() -> None:
    load_dotenv(ROOT / ".env", override=False)


def load_config(path: Path | None = None, overrides: dict[str, Any] | None = None) -> Config:
    """Load config.yaml, apply overrides, and resolve flags that depend on available keys."""
    load_env()
    raw = yaml.safe_load((path or CONFIG_PATH).read_text())
    if overrides:
        raw = _deep_merge(raw, overrides)
    cfg = Config.model_validate(raw)
    if cfg.flags.use_jev and not os.environ.get("TYPESAFE_API_KEY"):
        log.warning("TYPESAFE_API_KEY missing: use_jev=false, decisions use the Gemini fallback decider")
        cfg.flags.use_jev = False
    if cfg.flags.use_sarvam and not os.environ.get("SARVAM_API_KEY"):
        log.warning("SARVAM_API_KEY missing: use_sarvam=false, ASR uses the Gemini Transcribe fallback")
        cfg.flags.use_sarvam = False
    return cfg


def section_hash(*parts: Any) -> str:
    """Stable short hash of config sections / upstream digests, used as a stage cache key."""
    blob = json.dumps(
        [p.model_dump() if isinstance(p, BaseModel) else p for p in parts], sort_keys=True, default=str
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]
