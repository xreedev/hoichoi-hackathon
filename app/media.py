"""Thin ffmpeg/ffprobe helpers."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


class MediaError(RuntimeError):
    pass


def run(cmd: list[str]) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise MediaError(f"{cmd[0]} failed ({p.returncode}): {p.stderr.strip()[-800:]}")
    return p.stdout


def tool_version(tool: str) -> str:
    if shutil.which(tool) is None:
        raise MediaError(f"{tool} not on PATH")
    return run([tool, "-version"]).splitlines()[0]


def ffprobe(path: Path) -> dict:
    out = run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)])
    return json.loads(out)


def silent_wav(path: Path, seconds: float, sr: int) -> Path:
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"anullsrc=r={sr}:cl=mono",
         "-t", str(seconds), "-c:a", "pcm_s16le", str(path)])
    return path
