"""`python -m app.cli <module> ...` — every stage is runnable on its own."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from pathlib import Path

from app.config import Config, load_config

Handler = Callable[[argparse.Namespace, Config], int]
COMMANDS: dict[str, tuple[str, Callable[[argparse.ArgumentParser], None], Handler]] = {}


def command(name: str, help_: str, add_args: Callable[[argparse.ArgumentParser], None] | None = None):
    def deco(fn: Handler) -> Handler:
        COMMANDS[name] = (help_, add_args or (lambda p: None), fn)
        return fn
    return deco


def _video_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--video", required=True, help="path to the source video")
    p.add_argument("--force", action="store_true", help="ignore the stage cache and recompute")


@command("health", "check ffmpeg and every API key")
def _health(args: argparse.Namespace, cfg: Config) -> int:
    from app.modules import health

    return health.main(cfg)


@command("ingest", "M1: sha256, metadata, 360p proxy, 16 kHz WAV", _video_args)
def _ingest(args: argparse.Namespace, cfg: Config) -> int:
    from app.modules.ingest import ingest

    print(ingest(Path(args.video), cfg, force=args.force).model_dump_json(indent=2))
    return 0


def _meta(args: argparse.Namespace, cfg: Config):
    from app.modules.ingest import ingest

    return ingest(Path(args.video), cfg)


@command("shots", "M2: shot boundaries (PySceneDetect adaptive)", _video_args)
def _shots(args: argparse.Namespace, cfg: Config) -> int:
    from app.modules import shots

    out = shots.run(_meta(args, cfg), cfg, force=args.force)
    lens = sorted(s.end_s - s.start_s for s in out)
    print(f"{len(out)} shots; median length {lens[len(lens) // 2]:.1f}s")
    for s in out[:10]:
        print(f"  #{s.id:<4} {s.start_s:8.2f} → {s.end_s:8.2f}")
    return 0


def _pipeline(args: argparse.Namespace, cfg: Config):
    from app.pipeline import Pipeline

    def emit(event: str, data: dict) -> None:
        if event == "stage_done":
            print(f"  ✓ {data['stage']:<15} {data['seconds']:7.1f}s  {data['summary']}")
        elif event in ("warning", "error"):
            print(f"  ! {event} [{data.get('stage')}] {data.get('message')}")

    return Pipeline(cfg, emit, force=getattr(args, "force", False))


def _perceive(args: argparse.Namespace, cfg: Config):
    import asyncio

    return asyncio.run(_pipeline(args, cfg).perceive(Path(args.video)))


@command("scenes", "M5: semantic scenes (Gemini agentic video) — prints a scene table", _video_args)
def _scenes(args: argparse.Namespace, cfg: Config) -> int:
    from app.modules.scenes import mmss

    _, perc = _perceive(args, cfg)
    for s in perc["scenes"]:
        tags = ", ".join(f"{t.tag.value}:{t.confidence:.2f}" for t in s.sensitive_tags if t.tag.value != "none")
        print(f"{s.id:>3} {mmss(s.start_s)}–{mmss(s.end_s)} {s.mood:<8} T={s.narrative_tension:.1f} "
              f"{'CLIFF ' if s.ends_on_cliffhanger else ''}{s.dominant_activity} @ {s.setting} [{tags}]")
    return 0


@command("candidates", "M6 (+M8): break candidates at silent shot cuts", _video_args)
def _candidates(args: argparse.Namespace, cfg: Config) -> int:
    from app.modules.pacing import fmt_t

    _, perc = _perceive(args, cfg)
    for c in perc["candidates"]:
        print(f"#{c.id:<4} {fmt_t(c.t_s)}  pause {c.pause_before_s:.1f}+{c.pause_after_s:.1f}s  "
              f"scenes {c.prev_scene_id}→{c.next_scene_id}{'  [scene boundary]' if c.is_scene_boundary else ''}")
    return 0


def _run_args(p: argparse.ArgumentParser) -> None:
    _video_args(p)
    p.add_argument("--catalogue", help="brand catalogue JSON (default: paths.catalogue)")
    p.add_argument("--brands", help="comma-separated brand ids to consider (default: all)")


@command("run", "full pipeline → vmap.xml + debug.json", _run_args)
def _run(args: argparse.Namespace, cfg: Config) -> int:
    import asyncio

    from app.modules.pacing import fmt_t

    pipe = _pipeline(args, cfg)
    res = asyncio.run(pipe.run(Path(args.video), Path(args.catalogue) if args.catalogue else None,
                               args.brands.split(",") if args.brands else None))
    sel = [c for c in res.candidates if c.status == "selected"]
    print(f"\n{len(res.candidates)} candidates, {len(sel)} selected, {len(res.placements)} placements")
    for p in res.placements:
        print(f"  {p.break_id} @ {fmt_t(p.t_s)}  {p.brand_id} (p={p.brand_probability:.2f}, {p.ad_duration_s:g}s)"
              f"  blocked={[b.brand_id for b in p.blocked_brands]}\n     {p.brand_reason}")
    print(f"outputs: {cfg.runs_dir / res.meta.sha256}/vmap.xml, debug.json")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, (help_, add_args, _) in COMMANDS.items():
        add_args(sub.add_parser(name, help=help_))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "google_genai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    cfg = load_config()
    return COMMANDS[args.cmd][2](args, cfg)


if __name__ == "__main__":
    sys.exit(main())
