from __future__ import annotations

from pathlib import Path
from typing import Optional

from .config import ConfigError, load_config
from .watcher import process_stream


def main(config_path: Optional[Path] = None) -> None:
    """Run the watcher with a local JSON configuration file."""
    selected_path = config_path or Path.cwd() / "config.json"
    try:
        cfg = load_config(selected_path)
        process_stream(cfg)
    except ConfigError as exc:
        print(f"\n[CONFIG ERROR] {exc}")
        raise SystemExit(2) from exc
    except KeyboardInterrupt:
        print("\nStop requested.")
    except Exception as exc:
        print(f"\n[ERROR] {type(exc).__name__}: {exc}")
        raise
