"""M2 shots: PySceneDetect AdaptiveDetector on the proxy."""

from __future__ import annotations

import logging
from pathlib import Path

from app import store
from app.config import Config, section_hash
from app.schemas import Shot, VideoMeta

log = logging.getLogger(__name__)
STAGE = "shots"


def detect_shots(proxy_path: Path, duration_s: float) -> list[Shot]:
    from scenedetect import AdaptiveDetector, detect

    scene_list = detect(str(proxy_path), AdaptiveDetector())
    if not scene_list:
        return [Shot(id=0, start_s=0.0, end_s=duration_s)]
    shots = [Shot(id=i, start_s=round(s.get_seconds(), 3), end_s=round(e.get_seconds(), 3))
             for i, (s, e) in enumerate(scene_list)]
    return shots


def run(meta: VideoMeta, cfg: Config, force: bool = False, timings: dict[str, float] | None = None) -> list[Shot]:
    key = section_hash(STAGE, cfg.shots, store.stage_digest(cfg.runs_dir, meta.sha256, "ingest"))

    def compute() -> list[Shot]:
        shots = detect_shots(Path(meta.proxy_path), meta.duration_s)
        log.info("shots: %d shots", len(shots))
        return shots

    return store.cached_stage(cfg.runs_dir, meta.sha256, STAGE, key, list[Shot], compute, force, timings)
