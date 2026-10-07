#!/usr/bin/env python3
"""Dusky Converter — Python 3.15+ / FFmpeg CLI."""

import sys

if sys.version_info < (3, 15):
    raise SystemExit("Dusky Converter requires Python 3.15+. Check the python3 selected by your launcher PATH.")

from converter.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
