from app.modules.shots import detect_shots

FRAME = 1 / 25


def test_colour_cuts(color_shots_video):
    shots = detect_shots(color_shots_video, 20.0)
    cuts = [s.start_s for s in shots[1:]]
    assert len(cuts) == 3, shots
    for got, want in zip(cuts, (5.0, 10.0, 15.0), strict=True):
        assert abs(got - want) <= 2 * FRAME, (got, want)
    # contiguous, ordered, covering the clip
    assert shots[0].start_s == 0.0
    for a, b in zip(shots, shots[1:], strict=False):
        assert a.end_s == b.start_s
    assert abs(shots[-1].end_s - 20.0) <= 2 * FRAME
