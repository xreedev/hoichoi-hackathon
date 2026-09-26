"""M9 property-style tests on synthetic candidates."""

import random

from app.config import load_config
from app.modules import pacing
from app.schemas import Candidate, Scene, SensitiveTag

CFG = load_config()


def scene(i, a, b, tags=(("none", 0.95),), tension=0.2, cliff=False) -> Scene:
    return Scene(id=i, start_s=a, end_s=b, shot_ids=[], summary_en="", dominant_activity="", setting="",
                 mood="neutral", narrative_tension=tension,
                 sensitive_tags=[SensitiveTag(tag=t, confidence=p) for t, p in tags], ends_on_cliffhanger=cliff)


def cand(i, t, prev, nxt, pause=1.5, boundary=True, dec=None) -> Candidate:
    return Candidate(id=i, t_s=t, shot_boundary_id=i, pause_before_s=pause / 2, pause_after_s=pause / 2,
                     in_speech=False, in_transcript_chunk=False, prev_scene_id=prev, next_scene_id=nxt,
                     is_scene_boundary=boundary, decisions=dec or {})


def good_dec(**over):
    d = {"ends_scene": {"noul": 0.9}, "emotional_peak": {"noul": 0.1},
         "sensitive_context": {"choice": "none", "confidence": 0.95,
                               "probabilities": {"none": 0.97, "funeral": 0.01}},
         "break_quality": {"score": 4, "confidence": 0.8,
                           "probabilities": {"ideal": 0.7, "natural": 0.3}}}
    d.update(over)
    return d


def random_case(rng: random.Random):
    dur = rng.uniform(600, 5400)
    n_sc = rng.randint(3, 40)
    cuts = sorted(rng.uniform(0, dur) for _ in range(n_sc - 1))
    edges = [0.0, *cuts, dur]
    scenes = [scene(i, a, b, tags=(("none", 0.9),) if rng.random() < 0.7 else (("death", rng.random()),),
                    tension=rng.random(), cliff=rng.random() < 0.1)
              for i, (a, b) in enumerate(zip(edges, edges[1:], strict=False))]
    cands = []
    for i in range(rng.randint(0, 80)):
        t = rng.uniform(0, dur)
        k = next(j for j, s in enumerate(scenes) if s.start_s <= t < s.end_s or j == len(scenes) - 1)
        dec = good_dec(emotional_peak={"noul": rng.random()}, ends_scene={"noul": rng.random()})
        cands.append(cand(i, t, k, min(k + 1, len(scenes) - 1), pause=rng.uniform(0.8, 6),
                          boundary=rng.random() < 0.5, dec=dec))
    return dur, scenes, cands


def test_never_violates_pacing_rules():
    rng = random.Random(7)
    for _ in range(300):
        dur, scenes, cands = random_case(rng)
        out = pacing.apply(cands, scenes, dur, CFG)
        assert pacing.violations(out, dur, CFG) == []
        assert all(c.status in ("selected", "rejected") and c.reasons for c in out)


def test_sensitive_candidate_never_selected_even_with_top_score():
    scenes = [scene(0, 0, 600), scene(1, 600, 1200, tags=(("funeral", 0.3), ("none", 0.7)), tension=0.0),
              scene(2, 1200, 1800)]
    top = cand(0, 600.0, 0, 1, pause=10, dec=good_dec())  # best possible features, but next scene = funeral
    meh = cand(1, 1500.0, 1, 2, pause=0.9, boundary=False, dec=good_dec(ends_scene={"noul": 0.1}))
    ok = cand(2, 300.0, 0, 0, pause=0.9, boundary=False, dec=good_dec(ends_scene={"noul": 0.1}))
    out = {c.id: c for c in pacing.apply([top, meh, ok], scenes, 1800, CFG)}
    assert out[0].score >= out[2].score
    assert out[0].status == "rejected" and any("funeral" in r for r in out[0].reasons)
    assert out[1].status == "rejected"  # prev scene is the funeral scene
    assert out[2].status == "selected"


def test_m8_sensitive_probability_blocks():
    scenes = [scene(0, 0, 900), scene(1, 900, 1800)]
    dec = good_dec(sensitive_context={"choice": "none", "confidence": 0.8,
                                      "probabilities": {"none": 0.85, "violence_or_blood": 0.15}})
    out = pacing.apply([cand(0, 900.0, 0, 1, dec=dec)], scenes, 1800, CFG)
    assert out[0].status == "rejected" and any("violence_or_blood" in r for r in out[0].reasons)


def test_emotional_peak_and_cliffhanger_reject():
    scenes = [scene(0, 0, 900, cliff=True), scene(1, 900, 1800), scene(2, 1800, 2700)]
    a = cand(0, 900.0, 0, 1, dec=good_dec())  # prev ends on cliffhanger
    b = cand(1, 1800.0, 1, 2, dec=good_dec(emotional_peak={"noul": 0.61}))
    out = {c.id: c for c in pacing.apply([a, b], scenes, 2700, CFG)}
    assert out[0].status == "rejected" and any("cliffhanger" in r for r in out[0].reasons)
    assert out[1].status == "rejected" and any("emotional peak" in r for r in out[1].reasons)


def test_missing_scene_context_is_rejected():
    out = pacing.apply([cand(0, 500.0, None, None, dec=good_dec())], [], 1800, CFG)
    assert out[0].status == "rejected"


def test_deterministic():
    rng = random.Random(3)
    dur, scenes, cands = random_case(rng)
    a = pacing.apply(cands, scenes, dur, CFG)
    b = pacing.apply(cands, scenes, dur, CFG)
    assert [c.model_dump() for c in a] == [c.model_dump() for c in b]
    assert [c.model_dump() for c in cands] == [c.model_dump() for c in random_case(random.Random(3))[2]]


def test_rolling_window_cap():
    assert pacing.max_in_window([0, 100, 3599, 3600, 7300]) == 3
    assert pacing.max_in_window([]) == 0
    cfg = load_config(overrides={"pacing": {"min_gap_s": 60, "max_ad_load_pct": 100}})
    scenes = [scene(0, 0, 7200)]
    cands = [cand(i, 100.0 + 120 * i, 0, 0, dec=good_dec()) for i in range(20)]  # 20 cuts in 40 min
    out = pacing.apply(cands, scenes, 7200, cfg)
    assert sum(c.status == "selected" for c in out) == cfg.pacing.max_breaks_per_hour
    assert pacing.violations(out, 7200, cfg) == []
