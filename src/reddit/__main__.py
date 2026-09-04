"""Executable-module entry point for ``python -m reddit``."""

import sys

from reddit.cli import main

if __name__ == "__main__":
    sys.exit(main())
