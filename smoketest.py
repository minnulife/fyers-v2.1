"""Windows-friendly smoke-test entry point for FYERS Platform V2."""
from __future__ import annotations

import subprocess
import sys


def main() -> int:
    return subprocess.call([sys.executable, "-m", "pytest", "-q", "tests/test_smoke.py"])


if __name__ == "__main__":
    raise SystemExit(main())
