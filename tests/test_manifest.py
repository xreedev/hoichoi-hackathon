import re

from lxml import etree

from app import media
from app.modules import manifest as m11
from app.schemas import Brand, Placement, RunResult, VideoMeta

NS = {"vmap": m11.VMAP_NS, "v": m11.VAST_NS}
HMS = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3}$")


def brand(i: str) -> Brand:
    return Brand(id=f"b{i}", name=f"Test Brand {i}", category="cat", description="d",
                 target_contexts=["x"], negative_contexts=["funeral"])


def placements() -> list[Placement]:
    return [Placement(break_id="break-1", t_s=312.48, brand_id="b1", brand_probability=0.7, brand_reason="r",
                      ad_duration_s=15),
            Placement(break_id="break-2", t_s=3725.0, brand_id="b2", brand_probability=0.6, brand_reason="r",
                      ad_duration_s=20)]


def test_hms():
    assert m11.hms(0) == "00:00:00.000"
    assert m11.hms(312.48) == "00:05:12.480"
    assert m11.hms(3725.0) == "01:02:05.000"


def test_vmap_structure_and_offsets():
    ps = placements()
    brands = {b.id: b for b in (brand("1"), brand("2"))}
    xml = m11.build_vmap(ps, brands, {p.break_id: f"/media/{p.break_id}.mp4" for p in ps})
    root = etree.fromstring(xml)
    assert root.tag == f"{{{m11.VMAP_NS}}}VMAP" and root.get("version") == "1.0"
    breaks = root.findall("vmap:AdBreak", NS)
    assert [b.get("timeOffset") for b in breaks] == [m11.hms(p.t_s) for p in ps]
    for b, p in zip(breaks, ps, strict=True):
        assert b.get("breakType") == "linear" and b.get("breakId") == p.break_id
        src = b.find("vmap:AdSource", NS)
        assert src.get("allowMultipleAds") == "false" and src.get("followRedirects") == "true"
        vast = src.find("vmap:VASTAdData/v:VAST", NS)
        assert vast.get("version") == "4.2"
        inline = vast.find("v:Ad/v:InLine", NS)
        for tag in ("AdSystem", "AdTitle", "Impression"):
            assert inline.find(f"v:{tag}", NS) is not None, tag
        dur = inline.find("v:Creatives/v:Creative/v:Linear/v:Duration", NS).text
        assert HMS.match(dur) and dur == m11.hms(p.ad_duration_s)
        mf = inline.find("v:Creatives/v:Creative/v:Linear/v:MediaFiles/v:MediaFile", NS)
        assert mf.get("delivery") == "progressive" and mf.get("type") == "video/mp4"
        assert mf.get("width") and mf.get("height") and mf.text.strip().endswith(".mp4")


def test_write_generates_slates_and_debug(tmp_cfg, tmp_path):
    ps = placements()
    brands = {b.id: b for b in (brand("1"), brand("2"))}
    meta = VideoMeta(sha256="x", src_path="s", duration_s=4000, fps=25, width=1, height=1, proxy_path="p",
                     audio_path="a")
    rr = RunResult(meta=meta, config_snapshot={}, placements=ps)
    vmap, debug = m11.write(rr, brands, tmp_cfg, tmp_path)
    assert vmap.exists() and debug.exists()
    for p in rr.placements:
        info = media.ffprobe(p.creative_path)
        assert abs(float(info["format"]["duration"]) - p.ad_duration_s) < 0.2
    assert RunResult.model_validate_json(debug.read_text()).placements[0].break_id == "break-1"
