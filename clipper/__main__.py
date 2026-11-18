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
from .supervisor import Supervisor


def main() -> None:
    p = argparse.ArgumentParser(prog="clipper")
    p.add_argument("--config", default="config.yaml")
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
    for ch in cfg.channels:
        if not ch.allowed:
            log.warning("skipping %s: no permission note in config.yaml", ch.name)
    if not any(ch.allowed for ch in cfg.channels):
        sys.exit("no channels with a permission note in config.yaml")
    log.info("%.0f GB free; channels: %s", free_gb, ", ".join(c.name for c in cfg.channels if c.allowed))

    # Keep the Mac awake for as long as this process runs.
    caffeinate = subprocess.Popen(["caffeinate", "-dims", "-w", str(os.getpid())])
    try:
        asyncio.run(Supervisor(cfg, console).run())
    except KeyboardInterrupt:
        pass
    finally:
        caffeinate.terminate()


if __name__ == "__main__":
    main()
