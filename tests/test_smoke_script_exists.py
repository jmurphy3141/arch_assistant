"""
Unit test: assert scripts/agent3_smoke_v132.py exists and is executable.
No network calls are made.
"""

import os
import stat
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "agent3_smoke_v132.py"


def test_smoke_script_exists():
    assert SMOKE_SCRIPT.exists(), f"Expected script not found: {SMOKE_SCRIPT}"


def test_smoke_script_is_executable():
    mode = os.stat(SMOKE_SCRIPT).st_mode
    assert mode & stat.S_IXUSR, (
        f"{SMOKE_SCRIPT} is not user-executable (mode: {oct(mode)})"
    )


def test_smoke_script_has_shebang():
    first_line = SMOKE_SCRIPT.read_text(encoding="utf-8").splitlines()[0]
    assert first_line.startswith("#!"), (
        f"Expected shebang on line 1, got: {first_line!r}"
    )


def test_smoke_script_stdlib_only():
    """Ensure the script imports only stdlib modules (no third-party deps)."""
    source = SMOKE_SCRIPT.read_text(encoding="utf-8")
    # Reject any import of known third-party packages
    forbidden = ["import requests", "import httpx", "import aiohttp",
                 "import boto", "import oci"]
    for pkg in forbidden:
        assert pkg not in source, (
            f"Non-stdlib import found in smoke script: {pkg!r}"
        )
