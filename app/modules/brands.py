"""M10 brands: hard filters (tag match, keyword) → semantic negative-context filter → dynamic ranking → reason.

Brands come only from the catalogue file; nothing here names a brand. A 9th brand needs zero code changes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel
from typesafe_sdk import Choice, Noul

from app import store
from app.clients._common import is_mock
from app.clients.gemini import GeminiClient
from app.config import Config, section_hash
from app.decider import get_decider
from app.modules.pacing import fmt_t
from app.schemas import BlockedBrand, Brand, Candidate, Placement, Scene, VideoMeta

log = logging.getLogger(__name__)
STAGE = "brands"
MAPPED = {"brand_id", "display_name", "category", "target_contexts", "negative_contexts"}


# ---- catalogue --------------------------------------------------------------------------------------

def _shortest_creative(entry: dict[str, Any]) -> dict[str, Any] | None:
    cr = [c for c in entry.get("creatives") or [] if c.get("duration_sec")]
    return min(cr, key=lambda c: (float(c["duration_sec"]), str(c.get("id")))) if cr else None


def brand_from_entry(entry: dict[str, Any], base_dir: Path, root: Path) -> Brand:
    """Catalogue entry → Brand (mapping confirmed in Step 0: description = category, shortest creative)."""
    cr = _shortest_creative(entry)
    creative_path = None
    if cr and cr.get("url"):
        p = (base_dir / cr["url"]).resolve()
        creative_path = str(p.relative_to(root)) if p.is_relative_to(root) else str(p)
    return Brand(
        id=str(entry["brand_id"]), name=str(entry["display_name"]), category=str(entry["category"]),
        description=str(entry["category"]),
        target_contexts=[str(t).strip().lower() for t in entry.get("target_contexts") or []],
        negative_contexts=[str(t).strip().lower() for t in entry.get("negative_contexts") or []],
        creative_path=creative_path,
        extra={k: v for k, v in entry.items() if k not in MAPPED},
    )


def load_catalogue(path: Path, root: Path) -> list[Brand]:
    raw = json.loads(path.read_text())
    entries = raw.get("brands", raw) if isinstance(raw, dict) else raw
    brands = [brand_from_entry(e, path.parent, root) for e in entries]
    ids = [b.id for b in brands]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate brand ids in {path}")
    return brands


def ad_duration(brand: Brand, cfg: Config) -> float:
    cr = _shortest_creative(brand.extra)
    return float(cr["duration_sec"]) if cr else cfg.pacing.default_ad_duration_s


def load_synonyms(path: Path) -> dict[str, list[str]]:
    data = yaml.safe_load(path.read_text()) if path.exists() else None
    return {str(k): [str(v).strip().lower() for v in vs or []] for k, vs in (data or {}).items()}


# ---- hard filters (plain Python) --------------------------------------------------------------------

def sensitive_hits(prev: Scene, nxt: Scene, cand: Candidate, block_p: float) -> dict[str, dict[str, Any]]:
    """Every sensitive tag with p ≥ block_p around the cut (M5 prev/next tags, max-merged with M8)."""
    hits: dict[str, dict[str, Any]] = {}

    def add(tag: str, p: float, where: str) -> None:
        if tag != "none" and p >= block_p and p > hits.get(tag, {}).get("p", -1):
            hits[tag] = {"p": round(p, 4), "where": where}

    for label, sc in (("prev_scene", prev), ("next_scene", nxt)):
        for t in sc.sensitive_tags:
            add(t.tag.value, t.confidence, label)
    for tag, p in ((cand.decisions.get("sensitive_context") or {}).get("probabilities") or {}).items():
        add(tag, float(p), "m8")
    return hits


def tag_block(brand: Brand, hits: dict[str, dict[str, Any]], synonyms: dict[str, list[str]]) -> BlockedBrand | None:
    for neg in brand.negative_contexts:
        for tag, h in hits.items():
            if neg == tag or neg in synonyms.get(tag, []):
                return BlockedBrand(brand_id=brand.id, reason=f"negative context '{neg}' matches sensitive tag '{tag}'",
                                    evidence={"rule": "tag_match", "tag": tag, "negative_context": neg, **h})
    return None


def keyword_block(brand: Brand, prev: Scene, nxt: Scene) -> BlockedBrand | None:
    for label, sc in (("prev_scene", prev), ("next_scene", nxt)):
        for field in ("dominant_activity", "setting"):
            text = getattr(sc, field).lower()
            for neg in brand.negative_contexts:
                if re.search(rf"(?<!\w){re.escape(neg)}(?!\w)", text):
                    return BlockedBrand(
                        brand_id=brand.id, reason=f"negative context '{neg}' appears in {label} {field}",
                        evidence={"rule": "keyword", "negative_context": neg, "where": f"{label}.{field}",
                                  "text": getattr(sc, field)})
    return None


# ---- AI stages --------------------------------------------------------------------------------------

def scene_state(sc: Scene) -> dict[str, Any]:
    return {"summary": sc.summary_en, "activity": sc.dominant_activity, "setting": sc.setting, "mood": sc.mood}


async def semantic_filter(decider, cand: Candidate, prev: Scene, nxt: Scene, brands: list[Brand], block_p: float
                          ) -> tuple[list[Brand], list[BlockedBrand], dict[str, float], str]:
    """One decider request per break: a Noul per brand over its negative_contexts."""
    todo = [b for b in brands if b.negative_contexts]
    if not todo:
        return brands, [], {}, "none"
    questions = {f"neg_{i}": Noul(instructions="Does `prev_scene` or `next_scene` involve any of: "
                                               f"{', '.join(b.negative_contexts)}?")
                 for i, b in enumerate(todo)}
    state = {"prev_scene": scene_state(prev), "next_scene": scene_state(nxt)}
    r = await decider.system_one(f"negctx:c{cand.id}", state, questions)
    ps = {b.id: float(r.answers[f"neg_{i}"].noul) for i, b in enumerate(todo)}
    keep, blocked = [], []
    for b in brands:
        p = ps.get(b.id)
        if p is not None and p > block_p:
            blocked.append(BlockedBrand(brand_id=b.id, reason=f"semantic negative-context match p={p:.2f} > {block_p}",
                                        evidence={"rule": "semantic_noul", "p": round(p, 4),
                                                  "negative_contexts": b.negative_contexts, "source": r.source,
                                                  "model": r.model}))
        else:
            keep.append(b)
    return keep, blocked, ps, f"{r.source}:{r.model}"


async def rank(decider, cand: Candidate, prev: Scene, brands: list[Brand]) -> tuple[dict[str, float], str]:
    if len(brands) == 1:
        return {brands[0].id: 1.0}, "single survivor"
    q = {"brand": Choice(
        instructions="Which brand's advertising best fits the activity and setting of `prev_scene`?",
        criteria={b.id: f"{b.description}; relevant contexts: {', '.join(b.target_contexts)}" for b in brands})}
    state = {"prev_scene": {"activity": prev.dominant_activity, "setting": prev.setting}}
    r = await decider.system_one(f"rank:c{cand.id}", state, q)
    probs = {k: float(v) for k, v in r.answers["brand"].probabilities.items()}
    return probs, f"{r.source}:{r.model}"


def reason_sentence(gemini: GeminiClient, cfg: Config, brand: Brand, prev: Scene, nxt: Scene) -> str:
    if is_mock():
        return f"{brand.name} fits a break after a scene about {prev.dominant_activity}."
    prompt = (f"In one short English sentence, explain why an ad for {brand.name} ({brand.description}; "
              f"contexts: {', '.join(brand.target_contexts)}) fits an ad break placed right after a TV-drama "
              f"scene where the activity is '{prev.dominant_activity}' in '{prev.setting}', and before a scene "
              f"about '{nxt.dominant_activity}'. Return only the sentence.")
    it = gemini.create(f"reason_{brand.id}", cfg.timeouts.gemini_text_s, model=cfg.models.text, input=prompt,
                       generation_config={"thinking_level": "low"})
    return (it.output_text or "").strip()


# ---- per-break matching -----------------------------------------------------------------------------

async def match_break(cand: Candidate, prev: Scene, nxt: Scene, brands: list[Brand], cfg: Config,
                      synonyms: dict[str, list[str]], decider) -> dict[str, Any]:
    g = cfg.gates
    hits = sensitive_hits(prev, nxt, cand, g.sensitive_block_p)
    blocked: list[BlockedBrand] = []
    survivors: list[Brand] = []
    for b in brands:
        hit = tag_block(b, hits, synonyms) or keyword_block(b, prev, nxt)
        (blocked.append(hit) if hit else survivors.append(b))
    semantic_ps: dict[str, float] = {}
    sources: dict[str, str] = {}
    if survivors:
        survivors, sem_blocked, semantic_ps, sources["semantic_filter"] = await semantic_filter(
            decider, cand, prev, nxt, survivors, g.negative_context_noul_block_p)
        blocked += sem_blocked
    ranking: dict[str, float] = {}
    winner = None
    if survivors:
        ranking, sources["ranking"] = await rank(decider, cand, prev, survivors)
        winner = max(survivors, key=lambda b: (ranking.get(b.id, 0.0), -brands.index(b)))
    return {"considered": [b.id for b in brands], "sensitive_hits": hits, "blocked": blocked,
            "semantic_p": semantic_ps, "ranking": ranking, "winner": winner, "sources": sources}


class BrandStage(BaseModel):
    candidates: list[Candidate]
    placements: list[Placement]


async def match_all(meta: VideoMeta, candidates: list[Candidate], scenes: list[Scene], brands: list[Brand],
                    cfg: Config, raw_dir: Path | None = None) -> BrandStage:
    by_id = {s.id: s for s in scenes}
    synonyms = load_synonyms(cfg.path(cfg.paths.synonyms))
    decider = get_decider(cfg, raw_dir)
    gemini = GeminiClient(cfg, raw_dir=raw_dir)
    out = [c.model_copy(deep=True) for c in candidates]
    selected = sorted((c for c in out if c.status == "selected"), key=lambda c: c.t_s)

    async def one(c: Candidate) -> tuple[Candidate, dict[str, Any]]:
        prev, nxt = by_id.get(c.prev_scene_id), by_id.get(c.next_scene_id)
        if prev is None or nxt is None:
            return c, {"considered": [], "blocked": [], "winner": None, "ranking": {}, "sources": {}}
        return c, await match_break(c, prev, nxt, brands, cfg, synonyms, decider)

    results = await asyncio.gather(*(one(c) for c in selected))
    matched: list[tuple[Candidate, dict[str, Any]]] = []
    for c, m in results:
        c.decisions["brand_match"] = {
            "considered": m["considered"], "sensitive_hits": m.get("sensitive_hits", {}),
            "blocked": [b.model_dump() for b in m["blocked"]], "semantic_p": m.get("semantic_p", {}),
            "ranking": m["ranking"], "winner": m["winner"].id if m["winner"] else None, "sources": m["sources"]}
        if m["winner"] is None:
            c.status = "rejected"
            c.reasons.append("DROPPED no safe brand")
        else:
            matched.append((c, m))

    # Brand-diversity allocation: one break per brand at its best unclaimed slot.
    # Build per-brand candidate list from ranking scores across all matched candidates.
    brand_obj: dict[str, Brand] = {b.id: b for b in brands}
    brand_slots: dict[str, list[tuple[Candidate, float, dict[str, Any]]]] = {b.id: [] for b in brands}
    for c, m in matched:
        for bid, prob in m["ranking"].items():
            if prob > 0.01 and bid in brand_slots:
                brand_slots[bid].append((c, prob, m))
    for bid in brand_slots:
        brand_slots[bid].sort(key=lambda x: x[1], reverse=True)

    # Greedy: highest peak-confidence brand first, assign to best unclaimed slot.
    sorted_brands = sorted(
        [(bid, slots) for bid, slots in brand_slots.items() if slots],
        key=lambda kv: kv[1][0][1], reverse=True)
    used_times: list[float] = []
    assigned: list[tuple[Candidate, Brand, float, dict[str, Any]]] = []
    for bid, slots in sorted_brands:
        brand = brand_obj.get(bid)
        if brand is None:
            continue
        for cand, prob, m in slots:
            gap_ok = all(abs(cand.t_s - t) >= cfg.pacing.min_gap_s for t in used_times)
            if gap_ok:
                used_times.append(cand.t_s)
                assigned.append((cand, brand, prob, m))
                cand.decisions["brand_match"]["assigned_brand"] = bid
                break

    # Ad-load cap: drop lowest-confidence assignments first.
    def total_load(items: list[tuple[Candidate, Brand, float, dict[str, Any]]]) -> float:
        return sum(ad_duration(b, cfg) for _, b, _, _ in items) * 100.0 / meta.duration_s
    while assigned and total_load(assigned) > cfg.pacing.max_ad_load_pct:
        worst = min(assigned, key=lambda x: x[2])
        assigned.remove(worst)
        worst[0].status = "rejected"
        worst[0].reasons.append(f"DROPPED ad load > {cfg.pacing.max_ad_load_pct}%")

    assigned.sort(key=lambda x: x[0].t_s)
    reasons = await asyncio.gather(*(asyncio.to_thread(
        reason_sentence, gemini, cfg, brand, by_id[cand.prev_scene_id], by_id[cand.next_scene_id])
        for cand, brand, _, _ in assigned))
    placements: list[Placement] = []
    for n, ((cand, brand, prob, m), why) in enumerate(zip(assigned, reasons, strict=True), start=1):
        cand.decisions["brand_match"]["reason"] = why
        cand.reasons.append(f"BRAND {brand.id} p={prob:.2f} at {fmt_t(cand.t_s)} (diversity-assigned)")
        placements.append(Placement(
            break_id=f"break-{n}", t_s=cand.t_s, brand_id=brand.id,
            brand_probability=round(prob, 4), brand_reason=why,
            blocked_brands=m["blocked"], creative_path=brand.creative_path,
            ad_duration_s=ad_duration(brand, cfg)))
    total_ad = sum(p.ad_duration_s for p in placements)
    log.info("brands: %d candidates → %d placements covering %d brands (ad load %.1f%%)",
             len(selected), len(placements), len({p.brand_id for p in placements}),
             100.0 * total_ad / meta.duration_s)
    return BrandStage(candidates=out, placements=placements)


def run(meta: VideoMeta, candidates: list[Candidate], scenes: list[Scene], brands: list[Brand], cfg: Config,
        force: bool = False, timings: dict[str, float] | None = None) -> BrandStage:
    key = section_hash(STAGE, [b.model_dump() for b in brands], cfg.gates, cfg.pacing, cfg.models.text,
                       cfg.flags.use_jev, load_synonyms(cfg.path(cfg.paths.synonyms)),
                       [c.model_dump() for c in candidates])
    raw_dir = store.run_dir(cfg.runs_dir, meta.sha256) / "raw"
    return store.cached_stage(cfg.runs_dir, meta.sha256, STAGE, key, BrandStage,
                              lambda: asyncio.run(match_all(meta, candidates, scenes, brands, cfg, raw_dir)),
                              force, timings)


