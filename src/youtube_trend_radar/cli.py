from __future__ import annotations

import argparse
import logging
from pathlib import Path

from dotenv import load_dotenv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="youtube-trend-radar", description="Find fresh AI/developer YouTube topic opportunities.")
    parser.add_argument("--verbose", action="store_true", help="enable informational logging")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="collect, rank, and report current opportunities")
    scan.add_argument("--config", default="config.toml", help="TOML configuration path")
    scan.add_argument("--top", type=int, help="maximum number of Top Opportunities")
    scan.add_argument("--no-youtube", action="store_true", help="skip YouTube validation")

    doctor = subparsers.add_parser("doctor", help="check configuration, storage, credentials, and connectivity")
    doctor.add_argument("--config", default="config.toml", help="TOML configuration path")
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv(Path.cwd() / ".env")
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    # httpx logs full request URLs at INFO, including YouTube's API key query parameter.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    if args.command == "scan":
        from youtube_trend_radar.pipeline import run_scan

        return run_scan(Path(args.config), top=args.top, no_youtube=args.no_youtube)
    from youtube_trend_radar.doctor import run_doctor

    return run_doctor(Path(args.config))
