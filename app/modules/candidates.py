"""M6 candidates: deterministic gate. Only shot cuts inside real silence can ever become breaks."""

from __future__ import annotations

import bisect
import logging

from app import store
from app.config import CandidatesCfg, Config, section_hash
from app.modules.vad import silences
from app.schemas import Candidate, Scene, Shot, SpeechSegment, TranscriptChunk, VideoMeta

log = logging.getLogger(__name__)
STAGE = "candidates"


def _scene_at(scenes: list[Scene], t: float) -> int | None:
    for s in scenes:
        if s.start_s <= t < s.end_s:
            return s.id
    return None


def find_candidates(
    shots: list[Shot],
    speech: list[SpeechSegment],
    transcript: list[TranscriptChunk],
    scenes: list[Scene],
    duration_s: float,
    c: CandidatesCfg,
) -> list[Candidate]:
    """A candidate is a shot cut t where ALL hold (spec M6):
    VAD silence around t ≥ min_pause_s; ≥ clearance_s of silence on both sides of t;
    t not inside any transcript chunk; t outside the edge exclusion.
    """
    gaps = silences(speech, duration_s)
    gap_starts = [g[0] for g in gaps]
    edge = min(c.edge_exclusion_s, c.edge_exclusion_frac * duration_s)
    scene_bounds = [s.start_s for s in sorted(scenes, key=lambda s: s.start_s)[1:]]

    out: list[Candidate] = []
    for shot in sorted(shots, key=lambda s: s.start_s)[1:]:
        t = shot.start_s
        if t <= edge or t >= duration_s - edge:
            continue
        i = bisect.bisect_right(gap_starts, t) - 1
        if i < 0 or not (gaps[i][0] <= t <= gaps[i][1]):
            continue  # t is inside speech
        g0, g1 = gaps[i]
        before, after = t - g0, g1 - t
        if (g1 - g0) < c.min_pause_s or before < c.clearance_s or after < c.clearance_s:
            continue
        if any(ch.start_s <= t <= ch.end_s for ch in transcript):
            continue
        prev_id = _scene_at(scenes, t - c.clearance_s)
        next_id = _scene_at(scenes, t + c.clearance_s)
        on_boundary = any(abs(t - b) <= c.scene_boundary_tol_s for b in scene_bounds)
        out.append(Candidate(
            id=len(out), t_s=t, shot_boundary_id=shot.id,
            pause_before_s=round(before, 3), pause_after_s=round(after, 3),
            in_speech=False, in_transcript_chunk=False,
            prev_scene_id=prev_id, next_scene_id=next_id, is_scene_boundary=on_boundary,
        ))
    return out


def run(meta: VideoMeta, shots: list[Shot], speech: list[SpeechSegment], transcript: list[TranscriptChunk],
        scenes: list[Scene], cfg: Config, force: bool = False,
        timings: dict[str, float] | None = None) -> list[Candidate]:
    digests = [store.stage_digest(cfg.runs_dir, meta.sha256, s) for s in ("shots", "vad", "asr", "scenes")]
    key = section_hash(STAGE, cfg.candidates, *digests)

    def compute() -> list[Candidate]:
        cands = find_candidates(shots, speech, transcript, scenes, meta.duration_s, cfg.candidates)
        log.info("candidates: %d shots → %d candidates", len(shots), len(cands))
        return cands

    return store.cached_stage(cfg.runs_dir, meta.sha256, STAGE, key, list[Candidate], compute,
                              force, timings)
