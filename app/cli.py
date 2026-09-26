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
