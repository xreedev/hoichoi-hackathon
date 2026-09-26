"""FastAPI app: runs (upload or sample), SSE progress, results, VMAP/debug downloads, rematch, brands, UI."""

from __future__ import annotations

import asyncio
import io
import json
import re
import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.config import ROOT, Config, load_config
from app.modules import brands as m10
from app.pipeline import Pipeline

STATIC = Path(__file__).parent / "static"


@dataclass
class Run:
    id: str
    video: Path
    brand_ids: list[str] | None
    pacing: dict[str, float] | None
    status: str = "queued"  # queued | running | done | error
    events: list[dict[str, Any]] = field(default_factory=list)
    sha: str | None = None
    error: str | None = None


def create_app(cfg: Config) -> FastAPI:
    app = FastAPI(title="hoichoi ad breaks")
    runs: dict[str, Run] = {}
    cfg.runs_dir.mkdir(parents=True, exist_ok=True)
    assets = cfg.path("assets")

    # ---- catalogue: a working copy that "Add brand" / upload edit -------------------------------------
    def catalogue() -> Path:
        p = cfg.runs_dir / "_catalogue.json"
        if not p.exists():
            shutil.copy(cfg.path(cfg.paths.catalogue), p)
        return p

    def entries() -> list[dict[str, Any]]:
        raw = json.loads(catalogue().read_text())
        return raw.get("brands", raw) if isinstance(raw, dict) else raw

    @app.get("/api/brands")
    def list_brands() -> list[dict[str, Any]]:
        return [b.model_dump() for b in m10.load_catalogue(catalogue(), ROOT)]

    @app.post("/api/brands")
    def add_brand(entry: dict[str, Any]) -> dict[str, Any]:
        try:
            brand = m10.brand_from_entry(entry, catalogue().parent, ROOT)
        except (KeyError, TypeError, ValueError) as e:
            raise HTTPException(422, f"invalid brand: {e}") from e
        es = entries()
        if any(str(e["brand_id"]) == brand.id for e in es):
            raise HTTPException(409, f"brand {brand.id} already exists")
        catalogue().write_text(json.dumps([*es, entry], ensure_ascii=False, indent=2))
        return brand.model_dump()

    @app.post("/api/brands/upload")
    async def upload_brands(file: UploadFile = File(...)) -> dict[str, Any]:
        tmp = cfg.runs_dir / f"_upload_{uuid.uuid4().hex}.json"
        tmp.write_bytes(await file.read())
        try:
            brands = m10.load_catalogue(tmp, ROOT)
        except Exception as e:  # noqa: BLE001 - report any malformed catalogue to the client
            tmp.unlink(missing_ok=True)
            raise HTTPException(422, f"invalid catalogue: {e}") from e
        tmp.replace(catalogue())
        return {"brands": len(brands)}

    # ---- samples -----------------------------------------------------------------------------------------
    _THUMB_DIR = cfg.runs_dir / "_thumbs"

    def _duration(path: Path) -> float:
        try:
            out = subprocess.check_output(
                ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
                stderr=subprocess.DEVNULL)
            return float(json.loads(out)["format"]["duration"])
        except Exception:
            return 0.0

    def _has_cached_run(stem: str) -> bool:
        for p in cfg.runs_dir.glob("*/debug.json"):
            try:
                meta = json.loads(p.read_text()).get("meta", {})
                if Path(meta.get("src_path", "")).stem == stem:
                    return True
            except Exception:
                pass
        return False

    def _title(stem: str) -> str:
        return re.sub(r"[_\-]+", " ", stem).title()

    @app.get("/api/samples")
    def samples() -> list[dict[str, Any]]:
        result = []
        for ext in ("*.mp4", "*.mov", "*.mkv"):
            for p in sorted(assets.glob(ext)):
                result.append({
                    "id": p.stem, "title": _title(p.stem),
                    "duration_s": _duration(p), "has_cached_run": _has_cached_run(p.stem),
                })
        return result

    @app.get("/api/samples/{sid}/thumb.jpg")
    def sample_thumb(sid: str) -> Response:
        _THUMB_DIR.mkdir(parents=True, exist_ok=True)
        thumb = _THUMB_DIR / f"{sid}.jpg"
        if not thumb.exists():
            candidates = list(assets.glob(f"{sid}.*"))
            if not candidates:
                raise HTTPException(404, f"sample {sid} not found")
            src = candidates[0]
            t = max(0.0, _duration(src) * 0.10)
            subprocess.run(
                ["ffmpeg", "-ss", str(t), "-i", str(src), "-vframes", "1",
                 "-vf", "scale=320:-2", "-q:v", "5", str(thumb), "-y"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not thumb.exists():
            raise HTTPException(500, "thumbnail generation failed")
        return Response(thumb.read_bytes(), media_type="image/jpeg")

    # ---- chunked upload ----------------------------------------------------------------------------------
    _uploads: dict[str, dict[str, Any]] = {}

    @app.post("/api/uploads/start")
    async def upload_start(request: Request) -> dict[str, str]:
        body = await request.json()
        upload_id = uuid.uuid4().hex[:8]
        up_dir = cfg.runs_dir / "_uploads"
        up_dir.mkdir(exist_ok=True)
        filename = Path(body.get("filename", "video.mp4")).name
        path = up_dir / f"{upload_id}_{filename}"
        path.write_bytes(b"")
        _uploads[upload_id] = {"path": path, "total": int(body.get("size", 0)), "received": 0}
        return {"upload_id": upload_id}

    @app.post("/api/uploads/{uid}/chunk")
    async def upload_chunk(uid: str, request: Request) -> dict[str, Any]:
        if uid not in _uploads:
            raise HTTPException(404, "unknown upload")
        info = _uploads[uid]
        chunk = await request.body()
        with info["path"].open("ab") as f:
            f.write(chunk)
        info["received"] += len(chunk)
        return {"received": info["received"]}

    @app.get("/api/runs/{rid}/status")
    def run_status(rid: str) -> dict[str, Any]:
        run = get_run(rid)
        return {"run_id": rid, "status": run.status, "error": run.error, "events": list(run.events)}

    # ---- runs --------------------------------------------------------------------------------------------
    def work(run: Run) -> None:
        def emit(event: str, data: dict[str, Any]) -> None:
            if event == "done":
                run.sha = data["sha256"]
            run.events.append({"event": event, "data": data})

        run.status = "running"
        try:
            asyncio.run(Pipeline(cfg, emit).run(run.video, catalogue(), run.brand_ids, run.pacing))
            run.status = "done"
        except Exception as e:  # noqa: BLE001 - surfaced to the client via status + SSE
            run.error = f"{type(e).__name__}: {e}"
            run.events.append({"event": "error", "data": {"message": run.error}})
            run.status = "error"

    @app.post("/api/runs")
    async def create_run(video: UploadFile | None = File(None), sample_id: str | None = Form(None),
                         upload_id: str | None = Form(None),
                         brand_ids: str | None = Form(None), max_breaks_per_hour: int | None = Form(None),
                         min_gap_s: float | None = Form(None), max_ad_load_pct: float | None = Form(None)
                         ) -> dict[str, str]:
        rid = uuid.uuid4().hex[:8]
        if upload_id and upload_id in _uploads:
            path = _uploads[upload_id]["path"]
        elif video is not None and video.filename:
            up = cfg.runs_dir / "_uploads"
            up.mkdir(exist_ok=True)
            path = up / f"{rid}_{Path(video.filename).name}"
            with path.open("wb") as f:
                shutil.copyfileobj(video.file, f)
        elif sample_id:
            candidates = list(assets.glob(f"{Path(sample_id).stem}.*"))
            if not candidates:
                raise HTTPException(404, f"unknown sample {sample_id}")
            path = candidates[0]
        else:
            raise HTTPException(422, "send a video file, upload_id, or sample_id")
        pacing = {k: v for k, v in {"max_breaks_per_hour": max_breaks_per_hour, "min_gap_s": min_gap_s,
                                    "max_ad_load_pct": max_ad_load_pct}.items() if v is not None}
        ids = [b.strip() for b in brand_ids.split(",") if b.strip()] if brand_ids else None
        run = Run(rid, path, ids, pacing or None)
        runs[rid] = run
        threading.Thread(target=work, args=(run,), daemon=True).start()
        return {"run_id": rid}

    def get_run(rid: str) -> Run:
        if rid not in runs:
            raise HTTPException(404, f"unknown run {rid}")
        return runs[rid]

    def out_file(run: Run, name: str) -> Path:
        if run.sha is None:
            raise HTTPException(409, f"run {run.id} is {run.status}")
        return cfg.runs_dir / run.sha / name

    @app.get("/api/runs/{rid}/events")
    async def events(rid: str) -> StreamingResponse:
        run = get_run(rid)

        async def gen():
            i = 0
            while True:
                while i < len(run.events):
                    e = run.events[i]
                    i += 1
                    yield f"event: {e['event']}\ndata: {json.dumps(e['data'])}\n\n"
                if run.status in ("done", "error"):
                    return
                await asyncio.sleep(0.5)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    def result_payload(run: Run) -> dict[str, Any]:
        out: dict[str, Any] = {"id": run.id, "status": run.status, "error": run.error}
        if run.status == "done" and run.sha:
            base = f"/media/runs/{run.sha}"
            out.update(
                result=json.loads(out_file(run, "debug.json").read_text()),
                speech=json.loads(out_file(run, "vad.json").read_text()),
                proxy_url=f"{base}/proxy.mp4", vmap_url=f"/api/runs/{run.id}/vmap.xml",
                debug_url=f"/api/runs/{run.id}/debug.json")
        return out

    @app.get("/api/runs/{rid}")
    def get_result(rid: str) -> dict[str, Any]:
        return result_payload(get_run(rid))

    @app.get("/api/runs/{rid}/vmap.xml")
    def vmap(rid: str) -> FileResponse:
        return FileResponse(out_file(get_run(rid), "vmap.xml"), media_type="application/xml", filename="vmap.xml")

    @app.get("/api/runs/{rid}/debug.json")
    def debug(rid: str) -> FileResponse:
        return FileResponse(out_file(get_run(rid), "debug.json"), media_type="application/json",
                            filename="debug.json")

    @app.post("/api/runs/{rid}/rematch")
    def rematch(rid: str) -> dict[str, Any]:
        """Re-run M9–M11 against the current catalogue (perception comes from the cache)."""
        run = get_run(rid)
        if run.status != "done":
            raise HTTPException(409, f"run {rid} is {run.status}")
        asyncio.run(Pipeline(cfg).run(run.video, catalogue(), None, run.pacing))
        return result_payload(run)

    @app.get("/api/impression")
    def impression() -> Response:
        return Response(status_code=204)

    # ---- static: media + UI ---------------------------------------------------------------------------
    app.mount("/media/runs", StaticFiles(directory=cfg.runs_dir), name="runs")
    if assets.exists():
        app.mount("/media/assets", StaticFiles(directory=assets), name="assets")
    app.mount("/", StaticFiles(directory=STATIC, html=True), name="ui")
    return app


app = create_app(load_config())
