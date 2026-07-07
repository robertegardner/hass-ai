#!/usr/bin/env python3
"""Shim so `python scripts/smoke_test.py` works as documented; same as `pae smoke`."""

import sys

from pae.cli import main

if __name__ == "__main__":
    sys.exit(main(["smoke", *sys.argv[1:]]))
