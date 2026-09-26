from pathlib import Path

import soundfile as sf

from app import media
from app.modules.ingest import ingest


def test_ingest_synthetic(synthetic_av, tmp_cfg):
    meta = ingest(synthetic_av, tmp_cfg)
    assert abs(meta.duration_s - 20.0) <= 0.1
    assert meta.width == 640 and meta.height == 360
    assert abs(meta.fps - 25.0) < 0.01

    proxy = media.ffprobe(Path(meta.proxy_path))
    v = next(s for s in proxy["streams"] if s["codec_type"] == "video")
    assert int(v["height"]) == 360

    info = sf.info(meta.audio_path)
    assert info.samplerate == 16000
    assert info.channels == 1
    assert abs(info.duration - 20.0) <= 0.1


def test_ingest_is_cached(synthetic_av, tmp_cfg):
    first = ingest(synthetic_av, tmp_cfg)
    mtime = Path(first.proxy_path).stat().st_mtime
    second = ingest(synthetic_av, tmp_cfg)
    assert second == first
    assert Path(second.proxy_path).stat().st_mtime == mtime
