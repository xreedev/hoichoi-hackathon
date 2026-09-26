"""Synthetic media fixtures, generated with ffmpeg so tests never depend on the sample videos."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.config import Config, load_config


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


@pytest.fixture(scope="session")
def cfg() -> Config:
    return load_config()


@pytest.fixture
def tmp_cfg(tmp_path: Path) -> Config:
    """Config whose runs_dir is a temp dir, so tests never touch the real cache."""
    return load_config(overrides={"paths": {"runs_dir": str(tmp_path / "runs")}})


@pytest.fixture(scope="session")
def synthetic_av(tmp_path_factory) -> Path:
    """20 s 640x360 testsrc + 440 Hz sine (M1)."""
    out = tmp_path_factory.mktemp("media") / "synthetic_av.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc=duration=20:size=640x360:rate=25",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out))
    return out


@pytest.fixture(scope="session")
def color_shots_video(tmp_path_factory) -> Path:
    """Four solid-colour 5 s segments → hard cuts at 5/10/15 s (M2)."""
    out = tmp_path_factory.mktemp("media") / "color_shots.mp4"
    inputs: list[str] = []
    for c in ("red", "green", "blue", "yellow"):
        inputs += ["-f", "lavfi", "-i", f"color=c={c}:s=640x360:r=25:d=5"]
    ffmpeg(*inputs, "-filter_complex", "[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0[v]",
           "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out))
    return out


@pytest.fixture(scope="session")
def silence_wav(tmp_path_factory) -> Path:
    """10 s of 16 kHz mono silence (M3)."""
    out = tmp_path_factory.mktemp("media") / "silence.wav"
    ffmpeg("-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "10", "-c:a", "pcm_s16le", str(out))
    return out


@pytest.fixture(scope="session")
def silent_shots_video(tmp_path_factory) -> Path:
    """Four solid-colour 5 s shots with a silent audio track (end-to-end API test)."""
    out = tmp_path_factory.mktemp("media") / "silent_shots.mp4"
    inputs: list[str] = []
    for c in ("red", "green", "blue", "yellow"):
        inputs += ["-f", "lavfi", "-i", f"color=c={c}:s=640x360:r=25:d=5"]
    ffmpeg(*inputs, "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
           "-filter_complex", "[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0[v]", "-map", "[v]", "-map", "4:a",
           "-t", "20", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out))
    return out
