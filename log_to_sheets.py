#!/usr/bin/env python3
"""Backward-compatible command-line entry point."""

from pathlib import Path

from hytale_benchmark_tracker.cli import main as run


def main() -> None:
    run(Path(__file__).with_name("config.json"))


if __name__ == "__main__":
    main()
