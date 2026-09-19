#!/usr/bin/env python3
"""Dev entry point. The packaged build uses the same `main`."""
import sys

from muesli_win.app import main

if __name__ == "__main__":
    sys.exit(main())
