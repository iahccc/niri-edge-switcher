#!/usr/bin/env python3

import os

os.environ.setdefault("GSK_RENDERER", "gl")

from niri_edge_switcher.app import main


if __name__ == "__main__":
    raise SystemExit(main())
