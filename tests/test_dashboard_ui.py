"""Exercise dashboard JavaScript in Node's isolated VM; no real profiles/network."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_javascript():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the JavaScript behavioral suite")
    result = subprocess.run([node, "--test", str(ROOT / "tests/dashboard_ui.test.cjs")],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
