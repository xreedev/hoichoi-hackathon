"""M5 scenes: Gemini watches the proxy (agentic video) and groups shots into tagged semantic scenes."""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, Field

from app import store
from app.clients._common import is_mock, load_mock
from app.clients.gemini import GeminiClient
from app.config import Config, section_hash
from app.schemas import Mood, Scene, SensitiveTag, Shot, SpeechSegment, TranscriptChunk, VideoMeta
from app.taxonomy import tag_legend

log = logging.getLogger(__name__)
STAGE = "scenes"
PROMPT_VERSION = "v2"


class SceneOut(BaseModel):
    """What the model returns per scene; times/ids are derived from shots, never trusted from the model."""
    first_shot_id: int
    last_shot_id: int
    summary_en: str
    dominant_activity: str
    setting: str
    mood: Mood
    narrative_tension: float = Field(ge=0.0, le=1.0)
    sensitive_tags: list[SensitiveTag]
    ends_on_cliffhanger: bool


class ScenesResponse(BaseModel):
    scenes: list[SceneOut]


def mmss(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 60):02d}:{t % 60:04.1f}"


def _merge_speech(speech: list[SpeechSegment], join_gap_s: float) -> list[tuple[float, float]]:
    spans: list[tuple[float, float]] = []
    for s in sorted(speech, key=lambda s: s.start_s):
        if spans and s.start_s - spans[-1][1] <= join_gap_s:
            spans[-1] = (spans[-1][0], max(spans[-1][1], s.end_s))
        else:
            spans.append((s.start_s, s.end_s))
    return spans


def build_prompt(shots: list[Shot], speech: list[SpeechSegment], transcript: list[TranscriptChunk],
                 duration_s: float, join_gap_s: float) -> str:
    shot_lines = "\n".join(f"#{s.id} {mmss(s.start_s)}–{mmss(s.end_s)}" for s in shots)
    speech_lines = ", ".join(f"{mmss(a)}–{mmss(b)}" for a, b in _merge_speech(speech, join_gap_s))
    parts = [
        f"You are analysing one episode of a Bengali TV drama (duration {mmss(duration_s)}, MM:SS.s). "
        "The attached video is the episode.",
        f"SHOTS (id start–end), from a shot detector — {len(shots)} shots, ids 0..{len(shots) - 1}:\n"
        f"{shot_lines}",
        f"SPEECH (voice-activity spans, dialogue is happening inside these):\n{speech_lines or 'none'}",
    ]
    if transcript:
        gist = "\n".join(f"[{mmss(c.start_s)}] {c.text}" for c in transcript)
        parts.append(f"TRANSCRIPT (may be Bengali/English code-mix):\n{gist}")
    parts += [
        f"SENSITIVE TAGS (use exactly these values):\n{tag_legend()}",
        "TASK:\n"
        "1. Group consecutive shots into semantic scenes: a scene is one continuous dramatic unit — same "
        "place, time and situation. Start a new scene whenever the location, the time, the main activity, or "
        "the core situation changes; scenes typically last 30 s – 3 min, so split long stretches at those "
        "changes rather than merging them. "
        "Scenes must be contiguous, ordered and non-overlapping: the first scene starts at shot 0, each next "
        "scene starts at the shot right after the previous scene's last shot, and the last scene ends at "
        f"shot {len(shots) - 1}. Every shot belongs to exactly one scene.\n"
        "2. For each scene fill every field:\n"
        "   - first_shot_id, last_shot_id: inclusive shot ids from the SHOTS list.\n"
        "   - summary_en: 1–2 sentences in English describing what happens.\n"
        "   - dominant_activity: the main thing people are doing on screen, a short English phrase.\n"
        "   - setting: where it takes place, a short English phrase.\n"
        "   - mood: one of happy, neutral, tense, sad, romantic, comic, angry.\n"
        "   - narrative_tension: 0 (calm) to 1 (peak dramatic tension).\n"
        "   - sensitive_tags: ALWAYS report EVERY plausible sensitive tag with a confidence 0–1, even if the "
        "confidence is low; never omit an uncertain tag. Use tag \"none\" only for the confidence that "
        "nothing sensitive is present.\n"
        "   - ends_on_cliffhanger: true if the scene ends on an unresolved dramatic hook or reveal.\n"
        "All text fields must be in English.",
    ]
    return "\n\n".join(parts)


def repair(resp: ScenesResponse, shots: list[Shot], duration_s: float) -> tuple[list[Scene], list[str]]:
    """Force the invariants: contiguous, non-overlapping, full coverage, each shot in exactly one scene."""
    warnings: list[str] = []
    n = len(shots)
    items = sorted(resp.scenes, key=lambda s: s.first_shot_id)
    starts: list[int] = []
    kept: list[SceneOut] = []
    for s in items:
        first = min(max(s.first_shot_id, 0), n - 1)
        if starts and first <= starts[-1]:
            warnings.append(f"scenes: dropped duplicate/overlapping scene starting at shot {s.first_shot_id}")
            continue
        starts.append(first)
        kept.append(s)
    if not kept:
        raise ValueError("model returned no scenes")
    if starts[0] != 0:
        warnings.append(f"scenes: first scene started at shot {starts[0]}; extended to shot 0")
        starts[0] = 0
    scenes: list[Scene] = []
    for i, (s, first) in enumerate(zip(kept, starts, strict=True)):
        last = starts[i + 1] - 1 if i + 1 < len(starts) else n - 1
        if s.last_shot_id != last:
            warnings.append(f"scenes: scene {i} last_shot_id {s.last_shot_id} → {last} (made contiguous)")
        scenes.append(Scene(
            id=i, start_s=shots[first].start_s,
            end_s=duration_s if last == n - 1 else shots[last].end_s,
            shot_ids=list(range(first, last + 1)),
            summary_en=s.summary_en, dominant_activity=s.dominant_activity, setting=s.setting,
            mood=s.mood, narrative_tension=s.narrative_tension,
            sensitive_tags=s.sensitive_tags, ends_on_cliffhanger=s.ends_on_cliffhanger,
        ))
    return scenes, warnings


def call_model(client: GeminiClient, cfg: Config, file_rec: dict[str, str], prompt: str) -> dict:
    """Agentic video first; static 0.5 fps fallback. Returns the stream_text result plus 'processing'."""
    if is_mock():
        return load_mock("scenes")
    base = {
        "model": cfg.models.scene_video,
        "response_format": {"type": "text", "mime_type": "application/json",
                            "schema": ScenesResponse.model_json_schema()},
        "generation_config": {"thinking_level": "medium"},
    }
    video = {"type": "video", "uri": file_rec["uri"], "mime_type": file_rec["mime_type"], "resolution": "low"}
    try:
        out = client.stream_text("scenes_agentic", cfg.timeouts.gemini_video_s,
                                 input=[{**video, "processing": "agentic"}, {"type": "text", "text": prompt}],
                                 **base)
        ran = {"processing_call", "processing_result"} & set(out["step_types"])
        log.info("scenes: agentic processing steps seen: %s", sorted(ran) or "none")
        out["processing"] = "agentic" if ran else "agentic(no processing steps seen)"
        ScenesResponse.model_validate_json(out["text"])
        return out
    except Exception as e:  # noqa: BLE001 - documented fallback
        log.warning("scenes: agentic processing failed (%s); falling back to static 0.5 fps", e)
        out = client.stream_text("scenes_static", cfg.timeouts.gemini_video_s,
                                 input=[{**video, "processing": {"type": "static", "fps": 0.5}},
                                        {"type": "text", "text": prompt}], **base)
        out["processing"] = f"static fallback ({type(e).__name__})"
        return out


def segment(meta: VideoMeta, shots: list[Shot], speech: list[SpeechSegment],
            transcript: list[TranscriptChunk], cfg: Config, warnings: list[str] | None = None,
            model_versions: dict[str, str] | None = None) -> list[Scene]:
    rd = store.run_dir(cfg.runs_dir, meta.sha256)
    client = GeminiClient(cfg, raw_dir=rd / "raw")
    file_rec = client.upload_cached(Path(meta.proxy_path), rd / "gemini_file.json")
    prompt = build_prompt(shots, speech, transcript, meta.duration_s, cfg.candidates.min_pause_s)
    out = call_model(client, cfg, file_rec, prompt)
    resp = ScenesResponse.model_validate_json(out["text"])
    scenes, warns = repair(resp, shots, meta.duration_s)
    if not out.get("processing", "").startswith("agentic"):
        warns.append(f"scenes: {out.get('processing')}")
    for w in warns:
        log.warning(w)
    if warnings is not None:
        warnings.extend(warns)
    if model_versions is not None:
        model_versions["scenes"] = f"{out.get('model')} [{out.get('processing')}]"
    return scenes


def run(meta: VideoMeta, shots: list[Shot], speech: list[SpeechSegment], transcript: list[TranscriptChunk],
        cfg: Config, force: bool = False, timings: dict[str, float] | None = None,
        warnings: list[str] | None = None, model_versions: dict[str, str] | None = None) -> list[Scene]:
    digests = [store.stage_digest(cfg.runs_dir, meta.sha256, s) for s in ("shots", "vad", "asr")]
    key = section_hash(STAGE, PROMPT_VERSION, cfg.models.scene_video, *digests)
    return store.cached_stage(
        cfg.runs_dir, meta.sha256, STAGE, key, list[Scene],
        lambda: segment(meta, shots, speech, transcript, cfg, warnings, model_versions), force, timings)
