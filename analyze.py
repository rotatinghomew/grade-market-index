#!/usr/bin/env python3
"""仓库根目录入口：python3 analyze.py --help。"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from quality_index.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
