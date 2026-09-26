"""M11 manifest: VMAP 1.0.1 with inline VAST 4.2 ads, slate creatives, and debug.json."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from lxml import etree

from app import media
from app.config import Config
from app.schemas import Brand, Placement, RunResult

log = logging.getLogger(__name__)

VMAP_NS = "http://www.iab.net/videosuite/vmap"
VAST_NS = "http://www.iab.com/VAST"
SLATE_W, SLATE_H = 1280, 720


def hms(t: float) -> str:
    """Seconds → HH:MM:SS.mmm (VMAP timeOffset / VAST Duration)."""
    ms = int(round(t * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def slate_colour(brand_id: str) -> str:
    return "0x" + hashlib.sha256(brand_id.encode()).hexdigest()[:6]


def _escape_drawtext(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("%", "\\%")


def make_slate(brand: Brand, duration_s: float, out_dir: Path) -> Path:
    """Solid-colour slate with a silent audio track. Text overlay attempted; skipped if fontconfig unavailable."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{brand.id}_{duration_s:g}s.mp4"
    if out.exists():
        return out
    tmp = out.with_suffix(".tmp.mp4")
    colour = slate_colour(brand.id)
    common = ["ffmpeg", "-y", "-v", "error",
              "-f", "lavfi", "-i", f"color=c={colour}:s={SLATE_W}x{SLATE_H}:d={duration_s}:r=25",
              "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
              "-t", str(duration_s), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"]
    # try with drawtext; fall back to plain colour slate if fontconfig missing (Windows)
    text = _escape_drawtext(brand.name)
    vf = (f"drawtext=text='{text}':fontcolor=white:fontsize=96:borderw=4:bordercolor=black"
          ":x=(w-text_w)/2:y=(h-text_h)/2")
    try:
        media.run(common[:-1] + ["-vf", vf, "-shortest", str(tmp)])
    except Exception:
        if tmp.exists():
            tmp.unlink()
        media.run(common + [str(tmp)])
    tmp.rename(out)
    return out


def resolve_creative(p: Placement, brand: Brand, cfg: Config) -> Path:
    """Use the catalogue creative if the file exists, else a generated slate of the same duration."""
    if p.creative_path:
        cp = cfg.path(p.creative_path)
        if cp.exists():
            return cp
    return make_slate(brand, p.ad_duration_s, cfg.runs_dir / "_creatives")


def media_url(path: Path, cfg: Config, base_url: str) -> str:
    try:
        rel = path.resolve().relative_to(cfg.runs_dir.resolve())
        return f"{base_url}/media/runs/{rel.as_posix()}"
    except ValueError:
        rel = path.resolve().relative_to(cfg.path(".").resolve())
        return f"{base_url}/media/{rel.as_posix()}"


def _vast(p: Placement, brand: Brand, url: str, base_url: str) -> etree._Element:
    vast = etree.Element(f"{{{VAST_NS}}}VAST", nsmap={None: VAST_NS}, version="4.2")
    ad = etree.SubElement(vast, f"{{{VAST_NS}}}Ad", id=f"{p.break_id}-{brand.id}")
    inline = etree.SubElement(ad, f"{{{VAST_NS}}}InLine")
    etree.SubElement(inline, f"{{{VAST_NS}}}AdSystem", version="1.0").text = "hoichoi-adbreaks"
    etree.SubElement(inline, f"{{{VAST_NS}}}AdServingId").text = f"{p.break_id}-{brand.id}"
    etree.SubElement(inline, f"{{{VAST_NS}}}AdTitle").text = brand.name
    imp = etree.SubElement(inline, f"{{{VAST_NS}}}Impression", id=f"imp-{p.break_id}")
    imp.text = etree.CDATA(f"{base_url}/api/impression?break={p.break_id}&brand={brand.id}")
    creatives = etree.SubElement(inline, f"{{{VAST_NS}}}Creatives")
    cr = etree.SubElement(creatives, f"{{{VAST_NS}}}Creative", id=f"cr-{p.break_id}", adId=brand.id)
    etree.SubElement(cr, f"{{{VAST_NS}}}UniversalAdId", idRegistry="unknown").text = brand.id
    lin = etree.SubElement(cr, f"{{{VAST_NS}}}Linear")
    etree.SubElement(lin, f"{{{VAST_NS}}}Duration").text = hms(p.ad_duration_s)
    mfs = etree.SubElement(lin, f"{{{VAST_NS}}}MediaFiles")
    mf = etree.SubElement(mfs, f"{{{VAST_NS}}}MediaFile", delivery="progressive", type="video/mp4",
                          width=str(SLATE_W), height=str(SLATE_H))
    mf.text = etree.CDATA(url)
    return vast


def build_vmap(placements: list[Placement], brands: dict[str, Brand], urls: dict[str, str],
               base_url: str = "") -> bytes:
    root = etree.Element(f"{{{VMAP_NS}}}VMAP", nsmap={"vmap": VMAP_NS}, version="1.0")
    for p in sorted(placements, key=lambda p: p.t_s):
        br = etree.SubElement(root, f"{{{VMAP_NS}}}AdBreak", timeOffset=hms(p.t_s), breakType="linear",
                              breakId=p.break_id)
        src = etree.SubElement(br, f"{{{VMAP_NS}}}AdSource", id=f"src-{p.break_id}", allowMultipleAds="false",
                               followRedirects="true")
        data = etree.SubElement(src, f"{{{VMAP_NS}}}VASTAdData")
        data.append(_vast(p, brands[p.brand_id], urls[p.break_id], base_url))
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def write(result: RunResult, brands: dict[str, Brand], cfg: Config, out_dir: Path,
          base_url: str = "") -> tuple[Path, Path]:
    """Resolve creatives, write vmap.xml and debug.json; returns their paths."""
    urls: dict[str, str] = {}
    for p in result.placements:
        path = resolve_creative(p, brands[p.brand_id], cfg)
        p.creative_path = str(path)
        urls[p.break_id] = media_url(path, cfg, base_url)
    vmap = out_dir / "vmap.xml"
    vmap.write_bytes(build_vmap(result.placements, brands, urls, base_url))
    debug = out_dir / "debug.json"
    debug.write_text(result.model_dump_json(indent=2))
    log.info("manifest: %d breaks → %s", len(result.placements), vmap)
    return vmap, debug
