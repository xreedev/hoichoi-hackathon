"""M10 tests: 9th brand, negative-context hard block, keyword block, grep for leaks. Deciders are mocked."""

import asyncio
import json
import re

from app.clients.gemini_decider import DeciderResponse, to_answers
from app.config import ROOT, load_config
from app.modules import brands as m10
from app.schemas import Candidate, Scene, SensitiveTag

CFG = load_config()
CATALOGUE = ROOT / "assets" / "brands.json"


class MockDecider:
    """Answers every negative-context Noul with `neg_p` and ranks `prefer` highest."""
    source = "mock"

    def __init__(self, prefer: str, neg_p: float = 0.01):
        self.prefer, self.neg_p, self.calls = prefer, neg_p, []

    async def system_one(self, name, state, questions, media=None):
        self.calls.append((name, questions))
        raw = {}
        for k, q in questions.items():
            if q.type == "noul":
                raw[k] = {"p_yes": self.neg_p}
            else:
                raw[k] = {lbl: (0.9 if lbl == self.prefer else 0.1 / max(1, len(q.criteria) - 1))
                          for lbl in q.criteria}
        return DeciderResponse(model="mock", answers=to_answers(raw, questions), source="mock")


def scene(i, activity="talking at home", setting="living room", tags=(("none", 0.95),)) -> Scene:
    return Scene(id=i, start_s=i * 100.0, end_s=(i + 1) * 100.0, shot_ids=[i], summary_en=activity,
                 dominant_activity=activity, setting=setting, mood="neutral", narrative_tension=0.2,
                 sensitive_tags=[SensitiveTag(tag=t, confidence=p) for t, p in tags], ends_on_cliffhanger=False)


def cand() -> Candidate:
    return Candidate(id=0, t_s=100.0, shot_boundary_id=1, pause_before_s=1, pause_after_s=1, in_speech=False,
                     in_transcript_chunk=False, prev_scene_id=0, next_scene_id=1, is_scene_boundary=True,
                     status="selected", score=0.5)


def match(brands, prev, nxt, decider):
    return asyncio.run(m10.match_break(cand(), prev, nxt, brands, CFG, {}, decider))


def test_catalogue_mapping():
    brands = m10.load_catalogue(CATALOGUE, ROOT)
    assert len(brands) == 8
    raw = json.loads(CATALOGUE.read_text())[0]
    b = brands[0]
    assert (b.id, b.name, b.category, b.description) == (raw["brand_id"], raw["display_name"], raw["category"],
                                                         raw["category"])
    assert b.extra["creatives"] == raw["creatives"]
    assert m10.ad_duration(b, CFG) == min(c["duration_sec"] for c in raw["creatives"])


def test_ninth_brand_is_matchable_without_code_changes(tmp_path):
    entries = json.loads(CATALOGUE.read_text())
    entries.append({"brand_id": "brand_new9", "display_name": "Ninth Test Brand", "category": "pets/pet food",
                    "target_contexts": ["pet", "dog", "cat", "feeding pets"], "negative_contexts": ["funeral"],
                    "creatives": [{"id": "n9", "duration_sec": 10, "language": "bn", "url": "ads/n9.mp4"}]})
    cat = tmp_path / "brands.json"
    cat.write_text(json.dumps(entries))
    brands = m10.load_catalogue(cat, tmp_path)
    assert len(brands) == 9
    m = match(brands, scene(0, "feeding a pet dog"), scene(1), MockDecider(prefer="brand_new9"))
    assert m["winner"].id == "brand_new9"
    assert m10.ad_duration(m["winner"], CFG) == 10


def test_negative_context_blocks_even_if_ranker_prefers_it():
    brands = m10.load_catalogue(CATALOGUE, ROOT)
    target = next(b for b in brands if "funeral" in b.negative_contexts)
    funeral = scene(1, "family gathering", "house", tags=(("funeral", 0.8), ("none", 0.2)))
    m = match(brands, scene(0), funeral, MockDecider(prefer=target.id))
    assert m["winner"] is None or m["winner"].id != target.id
    blocked = {b.brand_id: b for b in m["blocked"]}
    assert blocked[target.id].evidence["rule"] == "tag_match"
    assert blocked[target.id].evidence["tag"] == "funeral" and blocked[target.id].evidence["p"] == 0.8


def test_keyword_block_on_activity():
    brands = m10.load_catalogue(CATALOGUE, ROOT)
    eaters = [b for b in brands if "eating" in b.negative_contexts]
    assert eaters
    m = match(brands, scene(0, "eating dinner with family", "dining room"), scene(1),
              MockDecider(prefer=eaters[0].id))
    assert m["winner"].id not in {b.id for b in eaters}
    assert all(any(x.brand_id == b.id and x.evidence["rule"] == "keyword" for x in m["blocked"]) for b in eaters)


def test_semantic_filter_blocks_and_no_survivor_drops():
    brands = m10.load_catalogue(CATALOGUE, ROOT)
    m = match(brands, scene(0), scene(1), MockDecider(prefer=brands[0].id, neg_p=0.5))
    assert m["winner"] is None
    assert {b.brand_id for b in m["blocked"]} == {b.id for b in brands}
    assert all(b.evidence["rule"] == "semantic_noul" for b in m["blocked"])


def test_one_semantic_request_per_break():
    brands = m10.load_catalogue(CATALOGUE, ROOT)
    d = MockDecider(prefer=brands[0].id)
    match(brands, scene(0), scene(1), d)
    names = [n for n, _ in d.calls]
    assert sum(n.startswith("negctx") for n in names) == 1
    assert sum(n.startswith("rank") for n in names) == 1


def _app_text() -> str:
    return "\n".join(p.read_text(errors="ignore") for p in (ROOT / "app").rglob("*")
                     if p.is_file() and p.suffix in {".py", ".js", ".html", ".css", ".yaml", ".json"})


def test_grep_no_brand_names_or_sample_timestamps_in_app():
    text = _app_text().lower()
    for e in json.loads(CATALOGUE.read_text()):
        for needle in (e["brand_id"], e["display_name"]):
            assert not re.search(rf"(?<![\w]){re.escape(needle.lower())}(?![\w])", text), needle
    # timestamps of any cached sample run must not be baked into code
    for f in (ROOT / "runs").glob("*/candidates.json"):
        for c in json.loads(f.read_text()):
            assert f"{c['t_s']:.2f}" not in text and f"{c['t_s']:.3f}" not in text
