"""M12 pipeline: M1 → (M2 ‖ M3 ‖ M4 ‖ Gemini upload) → M5 → M6 → (M7) → M8 → M9 → M10 → M11.

Perception stages are cached per video; `rematch` re-runs only M9–M11 so catalogue/pacing edits take seconds.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app import store
from app.config import Config, load_config
from app.schemas import Brand, Candidate, RunResult, Scene, VideoMeta

log = logging.getLogger(__name__)
Emit = Callable[[str, dict[str, Any]], None]


def _noop(event: str, data: dict[str, Any]) -> None:
    pass


class RunLog:
    """Per-video model versions + warnings, persisted so cached stages still report them."""

    def __init__(self, cfg: Config, sha: str):
        self.path = store.run_dir(cfg.runs_dir, sha) / "runlog.json"
        data = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.model_versions: dict[str, str] = data.get("model_versions", {})
        self.warnings: dict[str, list[str]] = data.get("warnings", {})

    def stage(self, name: str, warnings: list[str], versions: dict[str, str]) -> None:
        if warnings or name in self.warnings:
            self.warnings[name] = list(warnings)
        self.model_versions.update(versions)
        self.path.write_text(json.dumps({"model_versions": self.model_versions, "warnings": self.warnings}, indent=2))

    def all_warnings(self) -> list[str]:
        return [w for ws in self.warnings.values() for w in ws]


def filter_brands(brands: list[Brand], brand_ids: list[str] | None) -> list[Brand]:
    if not brand_ids:
        return brands
    wanted = set(brand_ids)
    return [b for b in brands if b.id in wanted]


class Pipeline:
    def __init__(self, cfg: Config | None = None, emit: Emit | None = None, force: bool = False):
        self.cfg = cfg or load_config()
        self.emit = emit or _noop
        self.force = force
        self.timings: dict[str, float] = {}

    # -- helpers ----------------------------------------------------------------------------------------
    async def _stage(self, name: str, fn: Callable[[], Any], count: Callable[[Any], str] | None = None) -> Any:
        self.emit("stage_start", {"stage": name})
        t0 = time.monotonic()
        try:
            out = await asyncio.to_thread(fn)
        except Exception as e:
            self.emit("error", {"stage": name, "message": f"{type(e).__name__}: {e}"})
            raise
        dt = round(time.monotonic() - t0, 3)
        self.timings.setdefault(name, dt)
        self.emit("stage_done", {"stage": name, "seconds": dt, "summary": count(out) if count else ""})
        return out

    def _warn(self, runlog: RunLog, stage: str, warnings: list[str]) -> None:
        for w in warnings:
            self.emit("warning", {"stage": stage, "message": w})

    # -- perception (cached per video) -------------------------------------------------------------------
    async def perceive(self, video: Path) -> tuple[VideoMeta, dict[str, Any]]:
        from app.clients.gemini import GeminiClient
        from app.modules import asr, candidates, decide, scenes, shots, vad
        from app.modules.ingest import ingest

        cfg, force = self.cfg, self.force
        meta: VideoMeta = await self._stage(
            "ingest", lambda: ingest(video, cfg, force, self.timings),
            lambda m: f"{m.duration_s:.0f}s {m.width}x{m.height}")
        runlog = RunLog(cfg, meta.sha256)
        rd = store.run_dir(cfg.runs_dir, meta.sha256)

        asr_warn: list[str] = []
        asr_versions: dict[str, str] = {}
        sh, sp, tr, _ = await asyncio.gather(
            self._stage("shots", lambda: shots.run(meta, cfg, force, self.timings), lambda o: f"{len(o)} shots"),
            self._stage("vad", lambda: vad.run(meta, cfg, force, self.timings),
                        lambda o: f"{len(o)} speech segments"),
            self._stage("asr", lambda: asr.run(meta, cfg, force, self.timings, asr_warn, asr_versions),
                        lambda o: f"{len(o)} transcript chunks"),
            self._stage("upload", lambda: GeminiClient(cfg, rd / "raw").upload_cached(
                Path(meta.proxy_path), rd / "gemini_file.json"), lambda o: "proxy on Gemini Files API"),
        )
        if asr_warn or asr_versions:
            runlog.stage("asr", asr_warn, asr_versions)
        self._warn(runlog, "asr", runlog.warnings.get("asr", []))

        sc_warn: list[str] = []
        sc_versions: dict[str, str] = {}
        sc: list[Scene] = await self._stage(
            "scenes", lambda: scenes.run(meta, sh, sp, tr, cfg, force, self.timings, sc_warn, sc_versions),
            lambda o: f"{len(sh)} shots → {len(o)} scenes")
        if sc_warn or sc_versions:
            runlog.stage("scenes", sc_warn, sc_versions)
        self._warn(runlog, "scenes", runlog.warnings.get("scenes", []))

        cands: list[Candidate] = await self._stage(
            "candidates", lambda: candidates.run(meta, sh, sp, tr, sc, cfg, force, self.timings),
            lambda o: f"{len(sh)} shots → {len(o)} candidates")

        if cfg.flags.use_vjepa:
            from app.modules import boundary_embed
            cands = await self._stage("boundary_embed",
                                      lambda: boundary_embed.run(meta, cands, cfg, force, self.timings),
                                      lambda o: f"{sum(c.visual_change is not None for c in o)} embedded")

        dec_warn: list[str] = []
        dec_versions: dict[str, str] = {}
        cands = await self._stage(
            "decide", lambda: decide.run(meta, cands, sc, cfg, force, self.timings, dec_warn, dec_versions),
            lambda o: f"{len(o)} candidates decided, {sum(c.escalated for c in o)} escalated")
        if dec_warn or dec_versions:
            runlog.stage("decide", dec_warn, dec_versions)
        self._warn(runlog, "decide", runlog.warnings.get("decide", []))
        return meta, {"shots": sh, "speech": sp, "transcript": tr, "scenes": sc, "candidates": cands,
                      "runlog": runlog}

    # -- decisioning (fast; re-run on catalogue / pacing change) -------------------------------------------
    async def place(self, meta: VideoMeta, perc: dict[str, Any], catalogue: Path | None = None,
                    brand_ids: list[str] | None = None, pacing_overrides: dict[str, Any] | None = None
                    ) -> RunResult:
        from app.config import ROOT
        from app.modules import brands as m10
        from app.modules import manifest, pacing

        cfg = self.cfg
        if pacing_overrides:
            cfg = load_config(overrides={"pacing": pacing_overrides,
                                         "paths": {"runs_dir": str(self.cfg.runs_dir)}})
        cat_path = catalogue or cfg.path(cfg.paths.catalogue)
        brands = filter_brands(m10.load_catalogue(cat_path, ROOT), brand_ids)
        sc, cands = perc["scenes"], perc["candidates"]

        paced = await self._stage(
            "pacing", lambda: pacing.apply(cands, sc, meta.duration_s, cfg),
            lambda o: f"{len(o)} candidates → {sum(c.status == 'selected' for c in o)} selected")
        store.save_stage(cfg.runs_dir, meta.sha256, "pacing", paced, "")
        matched = await self._stage(
            "brands", lambda: m10.run(meta, paced, sc, brands, cfg, self.force, self.timings),
            lambda o: f"{len(brands)} brands → {len(o.placements)} placements")
        runlog: RunLog = perc["runlog"]
        versions = dict(runlog.model_versions)
        versions.setdefault("decider", "jev" if cfg.flags.use_jev else f"gemini_fallback:{cfg.models.text}")
        result = RunResult(
            meta=meta, config_snapshot=cfg.model_dump(mode="json"), model_versions=versions,
            timings_s=self.timings, scenes=sc, candidates=matched.candidates, placements=matched.placements,
            warnings=runlog.all_warnings())
        out_dir = store.run_dir(cfg.runs_dir, meta.sha256)
        brand_map = {b.id: b for b in brands}
        await self._stage("manifest", lambda: manifest.write(result, brand_map, cfg, out_dir),
                          lambda o: f"{len(result.placements)} breaks → vmap.xml")
        self.emit("done", {"sha256": meta.sha256, "placements": len(result.placements)})
        return result

    async def run(self, video: Path, catalogue: Path | None = None, brand_ids: list[str] | None = None,
                  pacing_overrides: dict[str, Any] | None = None) -> RunResult:
        meta, perc = await self.perceive(video)
        return await self.place(meta, perc, catalogue, brand_ids, pacing_overrides)


def run(video_path: Path | str, catalogue_path: Path | str | None = None, brand_ids: list[str] | None = None,
        pacing_overrides: dict[str, Any] | None = None, cfg: Config | None = None, emit: Emit | None = None,
        force: bool = False) -> RunResult:
    p = Pipeline(cfg, emit, force)
    return asyncio.run(p.run(Path(video_path), Path(catalogue_path) if catalogue_path else None, brand_ids,
                             pacing_overrides))
