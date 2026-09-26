"""M6 on synthetic arrays — no media, no AI."""

from app.config import load_config
from app.modules.candidates import find_candidates
from app.schemas import Scene, Shot, SpeechSegment, TranscriptChunk

DUR = 600.0  # 10 min → edge exclusion = min(60, 30) = 30 s
C = load_config().candidates


def shots_at(*cuts: float) -> list[Shot]:
    edges = [0.0, *cuts, DUR]
    return [Shot(id=i, start_s=a, end_s=b) for i, (a, b) in enumerate(zip(edges, edges[1:], strict=False))]


def speech(*spans: tuple[float, float]) -> list[SpeechSegment]:
    return [SpeechSegment(start_s=a, end_s=b, source="silero") for a, b in spans]


def scene(i: int, a: float, b: float) -> Scene:
    return Scene(id=i, start_s=a, end_s=b, shot_ids=[], summary_en="", dominant_activity="", setting="",
                 mood="neutral", narrative_tension=0.1, sensitive_tags=[], ends_on_cliffhanger=False)


def ts(cands):
    return [c.t_s for c in cands]


def test_cut_during_speech_is_never_a_candidate():
    out = find_candidates(shots_at(100.0), speech((90, 110)), [], [], DUR, C)
    assert out == []


def test_cut_in_1_2s_silence_is_a_candidate():
    # speech ends 99.4, resumes 100.6 → 1.2 s gap, cut in the middle (0.6 s each side)
    out = find_candidates(shots_at(100.0), speech((50, 99.4), (100.6, 150)), [], [], DUR, C)
    assert ts(out) == [100.0]
    assert abs(out[0].pause_before_s - 0.6) < 1e-6 and abs(out[0].pause_after_s - 0.6) < 1e-6
    assert not out[0].in_speech and not out[0].in_transcript_chunk


def test_cut_in_0_5s_silence_is_not():
    out = find_candidates(shots_at(100.0), speech((50, 99.75), (100.25, 150)), [], [], DUR, C)
    assert out == []


def test_cut_inside_transcript_chunk_is_not():
    tr = [TranscriptChunk(start_s=98.0, end_s=103.0, text="…", source="sarvam")]
    out = find_candidates(shots_at(100.0), speech((50, 99.0), (101.0, 150)), tr, [], DUR, C)
    assert out == []


def test_clearance_required_on_both_sides():
    # 2 s gap but the cut sits 0.1 s after speech ends
    out = find_candidates(shots_at(100.1), speech((50, 100.0), (102.0, 150)), [], [], DUR, C)
    assert out == []


def test_edge_exclusion():
    out = find_candidates(shots_at(10.0, 590.0, 300.0), [], [], [], DUR, C)
    assert ts(out) == [300.0]


def test_scene_fields():
    scenes = [scene(0, 0, 200.3), scene(1, 200.3, DUR)]
    out = find_candidates(shots_at(200.0, 400.0), [], [], scenes, DUR, C)
    by_t = {c.t_s: c for c in out}
    assert by_t[200.0].is_scene_boundary and by_t[200.0].prev_scene_id == 0
    assert by_t[200.0].next_scene_id == 1
    assert not by_t[400.0].is_scene_boundary and by_t[400.0].prev_scene_id == 1
    # a cut within the tolerance but past the boundary still maps to the scenes on either side
    out = find_candidates(shots_at(200.7), [], [], scenes, DUR, C)
    assert out[0].is_scene_boundary and (out[0].prev_scene_id, out[0].next_scene_id) == (0, 1)
