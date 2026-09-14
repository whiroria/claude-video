#!/usr/bin/env python3
"""Convenience launcher; implementation remains inside the installable skill."""

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent / "skills" / "watch" / "scripts")
)
from vea.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
