"""M12: end-to-end through the API on a synthetic video, every AI client replayed from fixtures (MOCK=1)."""

import time

import pytest
from fastapi.testclient import TestClient
from lxml import etree

from app.api import create_app


@pytest.fixture
def client(tmp_cfg, monkeypatch):
    monkeypatch.setenv("MOCK", "1")
    return TestClient(create_app(tmp_cfg))


def wait(client, rid, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = client.get(f"/api/runs/{rid}").json()
        if r["status"] in ("done", "error"):
            return r
        time.sleep(0.5)
    raise TimeoutError(rid)


def test_end_to_end(client, silent_shots_video):
    with silent_shots_video.open("rb") as f:
        rid = client.post("/api/runs", files={"video": ("clip.mp4", f, "video/mp4")},
                          data={"min_gap_s": "1", "max_ad_load_pct": "100"}).json()["run_id"]
    r = wait(client, rid)
    assert r["status"] == "done", r
    res = r["result"]
    assert len(res["scenes"]) == 2
    assert [p["t_s"] for p in res["placements"]] == [10.0]  # the only cut at a scene boundary
    assert res["placements"][0]["brand_id"] == "brand_a"
    assert client.get(r["proxy_url"]).status_code == 200

    events = client.get(f"/api/runs/{rid}/events").text
    assert "event: stage_done" in events and "event: done" in events

    root = etree.fromstring(client.get(r["vmap_url"]).content)
    assert [b.get("timeOffset") for b in root] == ["00:00:10.000"]
    media = root.find(".//{http://www.iab.com/VAST}MediaFile").text.strip()
    assert client.get(media).status_code == 200
    assert client.get(r["debug_url"]).json()["placements"][0]["break_id"] == "break-1"

    # add a 9th brand, re-match: only M9–M11 re-run
    new = {"brand_id": "brand_new9", "display_name": "Ninth Brand", "category": "pets",
           "target_contexts": ["pets"], "negative_contexts": ["funeral"]}
    assert client.post("/api/brands", json=new).status_code == 200
    assert client.post("/api/brands", json=new).status_code == 409
    assert len(client.get("/api/brands").json()) == 9
    r2 = client.post(f"/api/runs/{rid}/rematch").json()
    assert r2["status"] == "done" and len(r2["result"]["placements"]) == 1


def test_bad_requests(client):
    assert client.post("/api/runs", data={}).status_code == 422
    assert client.post("/api/runs", data={"sample_id": "nope"}).status_code == 404
    assert client.get("/api/runs/nope").status_code == 404
    assert client.get("/").status_code == 200
