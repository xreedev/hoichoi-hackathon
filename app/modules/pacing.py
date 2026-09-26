"""M9 pacing: hard safety gates, weighted scoring, greedy selection under pacing rules. Pure Python."""

from __future__ import annotations

from typing import Any

from app.config import Config
from app.schemas import Candidate, Scene

BREAK_QUALITY_LABELS = ["jarring", "awkward", "acceptable", "natural", "ideal"]


def fmt_t(t: float) -> str:
    return f"{int(t // 60):02d}:{t % 60:04.1f}"


# ---- decision accessors (tolerant of a missing M8 stage) -------------------------------------------

def _noul(dec: dict[str, Any], q: str) -> float | None:
    v = dec.get(q)
    return None if v is None else float(v["noul"])


def m8_sensitive(dec: dict[str, Any]) -> tuple[str, float] | None:
    """Highest non-'none' sensitive probability from M8, as (tag, p)."""
    probs = (dec.get("sensitive_context") or {}).get("probabilities") or {}
    items = [(t, float(p)) for t, p in probs.items() if t != "none"]
    return max(items, key=lambda x: x[1]) if items else None


def break_quality_norm(dec: dict[str, Any]) -> float | None:
    """Expected label index / (n-1) from the Score probabilities → 0..1."""
    probs = (dec.get("break_quality") or {}).get("probabilities") or {}
    if not probs:
        return None
    n = len(BREAK_QUALITY_LABELS) - 1
    total = sum(float(probs.get(lbl, 0.0)) for lbl in BREAK_QUALITY_LABELS)
    if total <= 0:
        return None
    return sum(i * float(probs.get(lbl, 0.0)) for i, lbl in enumerate(BREAK_QUALITY_LABELS)) / (n * total)


def scene_sensitive(scene: Scene) -> tuple[str, float] | None:
    items = [(t.tag.value, t.confidence) for t in scene.sensitive_tags if t.tag.value != "none"]
    return max(items, key=lambda x: x[1]) if items else None


# ---- gates -----------------------------------------------------------------------------------------

def hard_reject_reasons(c: Candidate, scenes: dict[int, Scene], cfg: Config) -> list[str]:
    g = cfg.gates
    reasons: list[str] = []
    prev, nxt = scenes.get(c.prev_scene_id), scenes.get(c.next_scene_id)
    if prev is None or nxt is None:
        return ["REJECT no scene context around the cut (fail-safe)"]
    for label, sc in (("prev", prev), ("next", nxt)):
        hit = scene_sensitive(sc)
        if hit and hit[1] > g.sensitive_block_p:
            reasons.append(f"REJECT sensitive: {hit[0]} p={hit[1]:.2f} in {label} scene (M5) "
                           f"> {g.sensitive_block_p}")
    hit = m8_sensitive(c.decisions)
    if hit and hit[1] > g.sensitive_block_p:
        reasons.append(f"REJECT sensitive: {hit[0]} p={hit[1]:.2f} around the cut (M8) > {g.sensitive_block_p}")
    peak = _noul(c.decisions, "emotional_peak")
    if peak is not None and peak > g.emotional_peak_reject_p:
        reasons.append(f"REJECT emotional peak p={peak:.2f} > {g.emotional_peak_reject_p}")
    if g.cliffhanger_reject and prev.ends_on_cliffhanger:
        reasons.append("REJECT previous scene ends on a cliffhanger")
    return reasons


# ---- scoring ---------------------------------------------------------------------------------------

def features(c: Candidate, scenes: dict[int, Scene], cfg: Config) -> dict[str, float]:
    prev = scenes.get(c.prev_scene_id)
    return {
        "pause_len": min(1.0, (c.pause_before_s + c.pause_after_s) / cfg.scoring.pause_len_cap_s),
        "is_scene_boundary": 1.0 if c.is_scene_boundary else 0.0,
        "ends_scene_p": _noul(c.decisions, "ends_scene") or 0.0,
        "break_quality": break_quality_norm(c.decisions) or 0.0,
        "low_tension_prev": 1.0 - prev.narrative_tension if prev else 0.0,
        "visual_change": c.visual_change or 0.0,
    }


def score(feats: dict[str, float], cfg: Config) -> float:
    w = cfg.scoring_weights.model_dump()
    return round(sum(w[k] * v for k, v in feats.items()), 6)


# ---- selection -------------------------------------------------------------------------------------

HOUR_S = 3600.0


def max_in_window(times: list[float], window_s: float = HOUR_S) -> int:
    """Largest number of breaks inside any rolling window of `window_s` seconds."""
    ts, best, j = sorted(times), 0, 0
    for i, t in enumerate(ts):
        while t - ts[j] >= window_s:
            j += 1
        best = max(best, i - j + 1)
    return best


def ad_load_pct(n_breaks: int, ad_duration_s: float, duration_s: float) -> float:
    return 100.0 * n_breaks * ad_duration_s / duration_s


def apply(cands: list[Candidate], scenes: list[Scene], duration_s: float, cfg: Config,
          ad_duration_s: float | None = None) -> list[Candidate]:
    """Return scored copies of `cands` with status selected/rejected and a reason list each."""
    p = cfg.pacing
    ad_s = ad_duration_s if ad_duration_s is not None else p.default_ad_duration_s
    by_id = {s.id: s for s in scenes}
    out = [c.model_copy(deep=True) for c in cands]
    eligible: list[Candidate] = []
    for c in out:
        c.reasons = []
        feats = features(c, by_id, cfg)
        c.score = score(feats, cfg)
        c.reasons.append(f"score {c.score:.3f} = " + " + ".join(
            f"{k} {v:.2f}" for k, v in feats.items()))
        rej = hard_reject_reasons(c, by_id, cfg)
        if rej:
            c.status = "rejected"
            c.reasons.extend(rej)
        else:
            eligible.append(c)

    chosen: list[Candidate] = []
    for c in sorted(eligible, key=lambda c: (-c.score, c.t_s, c.id)):
        clash = next((s for s in chosen if abs(s.t_s - c.t_s) < p.min_gap_s), None)
        load = ad_load_pct(len(chosen) + 1, ad_s, duration_s)
        if max_in_window([s.t_s for s in chosen] + [c.t_s]) > p.max_breaks_per_hour:
            c.status, why = "rejected", f"SKIP would exceed {p.max_breaks_per_hour} breaks in a 60-min window"
        elif clash is not None:
            c.status, why = "rejected", f"SKIP within {p.min_gap_s:.0f}s of selected break at {fmt_t(clash.t_s)}"
        elif load > p.max_ad_load_pct:
            c.status, why = "rejected", f"SKIP ad load would be {load:.1f}% > {p.max_ad_load_pct}%"
        else:
            chosen.append(c)
            c.status, why = "selected", f"SELECTED rank {len(chosen)} (ad load {load:.1f}%)"
        c.reasons.append(why)
    return out


def violations(cands: list[Candidate], duration_s: float, cfg: Config, ad_duration_s: float | None = None
               ) -> list[str]:
    """Independent re-check of every pacing rule on the selected set (used by tests and M14 eval)."""
    p = cfg.pacing
    ad_s = ad_duration_s if ad_duration_s is not None else p.default_ad_duration_s
    sel = sorted((c for c in cands if c.status == "selected"), key=lambda c: c.t_s)
    errs = []
    if max_in_window([c.t_s for c in sel]) > p.max_breaks_per_hour:
        errs.append(f"{max_in_window([c.t_s for c in sel])} breaks in a 60-min window > {p.max_breaks_per_hour}")
    for a, b in zip(sel, sel[1:], strict=False):
        if b.t_s - a.t_s < p.min_gap_s:
            errs.append(f"gap {b.t_s - a.t_s:.1f}s < {p.min_gap_s}s between {a.t_s} and {b.t_s}")
    if sel and ad_load_pct(len(sel), ad_s, duration_s) > p.max_ad_load_pct:
        errs.append(f"ad load {ad_load_pct(len(sel), ad_s, duration_s):.1f}% > {p.max_ad_load_pct}%")
    return errs
