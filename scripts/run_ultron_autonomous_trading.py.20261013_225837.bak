#!/usr/bin/env python3
"""Repository-root launcher for ULTRON autonomous paper trading."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ultron.financial.trading.autonomous_runtime import main

if __name__ == "__main__":
    main()
