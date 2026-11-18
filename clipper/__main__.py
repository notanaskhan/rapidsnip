"""`uv run python -m clipper` starts everything."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

from .config import load_config


def main() -> None:
    p = argparse.ArgumentParser(prog="clipper")
    p.add_argument("--config", default="config.yaml")
    sub = p.add_subparsers(dest="cmd")
    serve = sub.add_parser("serve", help="run the API for the web UI (with live recording unless --no-live)")
    serve.add_argument("--no-live", action="store_true", help="don't record; API, clips and link jobs only")
    serve.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to allow other devices on your Wi-Fi")
    serve.add_argument("--port", type=int, default=8000)
    args = p.parse_args()

    cfg = load_config(args.config)
    console = Console()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(message)s", datefmt="%H:%M:%S",
        handlers=[RichHandler(console=console, show_path=False)],
    )
    file_log = logging.FileHandler(cfg.data_dir / "clipper.log")
    file_log.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
    logging.getLogger().addHandler(file_log)
    logging.getLogger("streamlink").setLevel(logging.WARNING)
    log = logging.getLogger("clipper")

    free_gb = shutil.disk_usage(cfg.data_dir).free / 1e9
    if free_gb < cfg.min_free_gb:
        sys.exit(f"only {free_gb:.0f} GB free; need at least {cfg.min_free_gb:.0f} GB")

    # Keep the Mac awake for as long as this process runs.
    caffeinate = subprocess.Popen(["caffeinate", "-dims", "-w", str(os.getpid())])
    try:
        if args.cmd == "serve":
            _serve(cfg, args, log)
        else:
            _cli(cfg, console, log)
    except KeyboardInterrupt:
        pass
    finally:
        caffeinate.terminate()


def _cli(cfg, console, log) -> None:
    # Imported here, not at the top: spawned helper processes re-import this module, and the
    # link analyzer's process must never load Streamlink (see clipper/links.py).
    from .supervisor import Supervisor

    for ch in cfg.channels:
        if not ch.allowed:
            log.warning("skipping %s: no permission note in config.yaml", ch.name)
        elif not ch.recordable:
            log.warning("skipping %s: live recording on %s isn't supported yet", ch.name, ch.platform)
    if not any(ch.recordable for ch in cfg.channels):
        sys.exit("no recordable channels in config.yaml")
    log.info("%.0f GB free; channels: %s", shutil.disk_usage(cfg.data_dir).free / 1e9,
             ", ".join(c.name for c in cfg.channels if c.recordable))
    asyncio.run(Supervisor(cfg, console).run())


def _serve(cfg, args, log) -> None:
    import uvicorn

    from .db import DB, import_config_channels
    from .links import LinkManager
    from .server import create_app

    db = DB(cfg.data_dir / "clipper.db")
    n = import_config_channels(db, cfg.channels)
    if n:
        log.info("imported %d streamers from config.yaml", n)
    app = create_app(cfg, db, live=not args.no_live, links=LinkManager(cfg, db))
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
