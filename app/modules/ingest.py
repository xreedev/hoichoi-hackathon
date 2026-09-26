"""M1 ingest: sha256, ffprobe metadata, 360p proxy and 16 kHz mono WAV."""

from __future__ import annotations

import hashlib
import logging
from fractions import Fraction
from pathlib import Path

from app import media, store
from app.config import Config, section_hash
from app.schemas import VideoMeta

log = logging.getLogger(__name__)
STAGE = "ingest"


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def probe(path: Path) -> dict:
    info = media.ffprobe(path)
    video = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    if video is None:
        raise media.MediaError(f"{path} has no video stream")
    rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"
    if rate in ("0/0", "0/1"):
        rate = video.get("r_frame_rate", "0/1")
    return {
        "duration_s": float(info["format"]["duration"]),
        "fps": float(Fraction(rate)) if rate not in ("0/0",) else 0.0,
        "width": int(video["width"]),
        "height": int(video["height"]),
    }


def make_proxy(src: Path, out: Path, height: int) -> Path:
    media.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", f"scale=-2:{height}",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
               "-c:a", "aac", "-b:a", "64k", str(out)])
    return out


def extract_audio(src: Path, out: Path, sr: int) -> Path:
    media.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(sr),
               "-c:a", "pcm_s16le", str(out)])
    return out


def ingest(src: Path, cfg: Config, force: bool = False, timings: dict[str, float] | None = None) -> VideoMeta:
    src = src.resolve()
    sha = sha256_file(src)
    rd = store.run_dir(cfg.runs_dir, sha)
    key = section_hash(STAGE, cfg.ingest)

    def compute() -> VideoMeta:
        meta = probe(src)
        proxy = make_proxy(src, rd / "proxy.mp4", cfg.ingest.proxy_height)
        audio = extract_audio(src, rd / "audio.wav", cfg.ingest.audio_sr)
        log.info("ingest %s: %.1fs %dx%d@%.2f", src.name, meta["duration_s"], meta["width"],
                 meta["height"], meta["fps"])
        return VideoMeta(sha256=sha, src_path=str(src), proxy_path=str(proxy), audio_path=str(audio), **meta)

    out = store.cached_stage(cfg.runs_dir, sha, STAGE, key, VideoMeta, compute, force, timings)
    if not (Path(out.proxy_path).exists() and Path(out.audio_path).exists()):
        out = store.cached_stage(cfg.runs_dir, sha, STAGE, key, VideoMeta, compute, True, timings)
    return out
