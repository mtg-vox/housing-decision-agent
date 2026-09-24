"""Compatibility shim: the dashboard now lives in housing_agent.web."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from housing_agent.web import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
