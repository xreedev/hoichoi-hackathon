from pathlib import Path

import pytest

from app.config import load_config
from app.modules import vad
from app.schemas import SpeechSegment

SAMPLE = Path(__file__).resolve().parent.parent / "assets" / "bhojon_bilashi.mp4"


def test_silence_has_no_speech(silence_wav, cfg):
    assert vad.detect_speech(silence_wav, cfg) == []


def test_silences_between_segments():
    segs = [SpeechSegment(start_s=1, end_s=2, source="silero"),
            SpeechSegment(start_s=3.5, end_s=4, source="silero")]
    assert vad.silences(segs, 5.0) == [(0.0, 1), (2, 3.5), (4, 5.0)]
    assert vad.silences([], 5.0) == [(0.0, 5.0)]


@pytest.mark.live
@pytest.mark.skipif(not SAMPLE.exists(), reason="sample video not downloaded")
def test_sample_speech_report():
    from app.modules.ingest import ingest

    cfg = load_config()
    meta = ingest(SAMPLE, cfg)
    segs = vad.run(meta, cfg)
    speech = sum(s.end_s - s.start_s for s in segs)
    gaps = sorted(vad.silences(segs, meta.duration_s), key=lambda g: g[0] - g[1])[:10]
    print(f"\n{SAMPLE.name}: {100 * speech / meta.duration_s:.1f}% speech, {len(segs)} segments")
    for a, b in gaps:
        print(f"  silence {a:8.2f} → {b:8.2f}  ({b - a:.2f}s)")
    assert segs
