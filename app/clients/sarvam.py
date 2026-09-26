"""Sarvam wrapper (batch speech-to-text). All Sarvam calls go through here."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from app.clients._common import retry_sync, save_raw
from app.config import Config

log = logging.getLogger(__name__)


def has_key() -> bool:
    return bool(os.environ.get("SARVAM_API_KEY"))


class SarvamClient:
    def __init__(self, cfg: Config, raw_dir: Path | None = None):
        self.cfg = cfg
        self.raw_dir = raw_dir

    def _client(self):
        from sarvamai import SarvamAI

        return SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"],
                        timeout=self.cfg.timeouts.sarvam_job_s)

    def batch_transcribe(self, audio_path: Path, out_dir: Path) -> dict[str, Any]:
        """Run one Saaras batch job; returns {"results": ..., "output_files": [...]}.

        Raises on job failure so callers can fall back.
        """
        c = self.cfg.concurrency
        t = self.cfg.timeouts
        client = self._client()

        def run() -> dict[str, Any]:
            job = client.speech_to_text_job.create_job(
                model=self.cfg.models.sarvam_asr, mode="codemix", language_code="bn-IN",
                with_diarization=True)
            job.upload_files(file_paths=[str(audio_path)], timeout=t.sarvam_job_s)
            job.start()
            job.wait_until_complete(poll_interval=int(t.sarvam_poll_s), timeout=int(t.sarvam_job_s))
            results = job.get_file_results()
            if not results.get("successful"):
                raise RuntimeError(f"Sarvam job {job.job_id} failed: {results.get('failed')}")
            out_dir.mkdir(parents=True, exist_ok=True)
            job.download_outputs(output_dir=str(out_dir))
            return {"job_id": job.job_id, "results": results,
                    "output_files": sorted(str(p) for p in out_dir.glob("*.json"))}

        out = retry_sync(run, c.retry_max_attempts, c.retry_base_delay_s, "sarvam batch")
        save_raw(self.raw_dir, "sarvam_job", out)
        return out
