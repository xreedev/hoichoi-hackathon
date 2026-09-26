import json
from pathlib import Path

import pytest

from app.modules import scenes as m5
from app.schemas import Scene, Shot

FIX = Path(__file__).parent / "fixtures" / "scenes_recorded.json"
SAMPLE = Path(__file__).resolve().parent.parent / "assets" / "bhojon_bilashi.mp4"


def assert_invariants(scenes: list[Scene], shots: list[Shot], duration: float):
    assert scenes[0].start_s == 0.0
    assert abs(scenes[-1].end_s - duration) < 1e-6
    for a, b in zip(scenes, scenes[1:], strict=False):
        assert a.end_s == b.start_s, (a.id, b.id)  # contiguous, non-overlapping
        assert a.start_s < a.end_s
    ids = [i for s in scenes for i in s.shot_ids]
    assert ids == list(range(len(shots)))  # every shot in exactly one scene, in order


def _shots(n: int, step: float = 2.0) -> list[Shot]:
    return [Shot(id=i, start_s=i * step, end_s=(i + 1) * step) for i in range(n)]


def _out(first: int, last: int, **kw) -> dict:
    return {"first_shot_id": first, "last_shot_id": last, "summary_en": "x", "dominant_activity": "talking",
            "setting": "room", "mood": "neutral", "narrative_tension": 0.2,
            "sensitive_tags": [{"tag": "none", "confidence": 0.9}], "ends_on_cliffhanger": False, **kw}


def test_recorded_response_validates_and_holds_invariants():
    fx = json.loads(FIX.read_text())
    shots = [Shot(**s) for s in fx["shots"]]
    resp = m5.ScenesResponse.model_validate_json(fx["text"])
    scenes, _ = m5.repair(resp, shots, fx["duration_s"])
    assert_invariants(scenes, shots, fx["duration_s"])
    assert {"processing_call", "processing_result"} <= set(fx["step_types"])
    for s in scenes:
        assert s.summary_en and s.sensitive_tags


def test_repair_fixes_gaps_overlaps_and_missing_start():
    shots = _shots(10)
    resp = m5.ScenesResponse.model_validate(
        {"scenes": [_out(4, 5), _out(1, 3), _out(3, 7), _out(8, 8)]})  # starts late, overlaps, gap at 9
    scenes, warns = m5.repair(resp, shots, 20.0)
    assert_invariants(scenes, shots, 20.0)
    assert warns


def test_prompt_contains_inputs_and_tag_legend():
    shots = _shots(3)
    p = m5.build_prompt(shots, [], [], 6.0, 0.8)
    assert "#2 00:04.0–00:06.0" in p
    assert "child_in_distress" in p and "never omit" in p


def test_schema_has_no_brand_or_timestamp_leak():
    schema = json.dumps(m5.ScenesResponse.model_json_schema())
    assert "first_shot_id" in schema and "sensitive_tags" in schema


@pytest.mark.live
@pytest.mark.skipif(not SAMPLE.exists(), reason="sample video not downloaded")
def test_sample_scene_table():
    from app.config import load_config
    from app.modules import shots as m2
    from app.modules import vad as m3
    from app.modules.ingest import ingest

    cfg = load_config()
    meta = ingest(SAMPLE, cfg)
    sh, sp = m2.run(meta, cfg), m3.run(meta, cfg)
    scenes = m5.run(meta, sh, sp, [], cfg)
    assert_invariants(scenes, sh, meta.duration_s)
    print()
    for s in scenes:
        tags = ", ".join(f"{t.tag.value}:{t.confidence:.2f}"
                         for t in s.sensitive_tags if t.tag.value != "none")
        print(f"{s.id:>3} {m5.mmss(s.start_s)}–{m5.mmss(s.end_s)} {s.mood:<8} T={s.narrative_tension:.1f} "
              f"{'CLIFF ' if s.ends_on_cliffhanger else ''}{s.dominant_activity} @ {s.setting} [{tags}]")
