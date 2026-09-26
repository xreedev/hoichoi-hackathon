import pytest

from app.config import load_config
from app.modules import health

KEYS = ("GEMINI_API_KEY", "SARVAM_API_KEY", "TYPESAFE_API_KEY")


def test_missing_keys_are_skipped_not_crashing(monkeypatch):
    cfg = load_config()
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    checks = {c.name: c for c in health.run_health(cfg)}
    assert checks["ffmpeg"].status == "OK"
    assert checks["ffprobe"].status == "OK"
    for name in ("gemini", "sarvam", "typesafe"):
        assert checks[name].status == "SKIPPED"
    assert "SKIPPED" in health.format_table(list(checks.values()))


def test_failing_provider_reports_fail(monkeypatch):
    cfg = load_config()
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setattr(health, "_gemini", lambda cfg: (_ for _ in ()).throw(RuntimeError("boom")))
    checks = {c.name: c for c in health.run_health(cfg)}
    assert checks["gemini"].status == "FAIL"
    assert "boom" in checks["gemini"].detail


def test_config_loads_and_jev_auto_disables(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    cfg = load_config()
    assert cfg.flags.use_jev is False
    assert cfg.models.scene_video == "gemini-3.8-flash"


@pytest.mark.live
def test_live_health():
    cfg = load_config()
    checks = health.run_health(cfg)
    assert not [c for c in checks if c.status == "FAIL"], health.format_table(checks)
