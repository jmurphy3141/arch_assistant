"""
agent/png_exporter.py
----------------------
Exports a .drawio file to PNG via the draw.io desktop CLI.

Requires draw.io CLI installed (the Dockerfile installs it).
Uses Xvfb for headless rendering.

Returns None if the CLI is not installed (graceful fallback — callers must handle None).
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DRAWIO_CLI = shutil.which("drawio") or shutil.which("draw.io")


def export_png(drawio_path: str | Path, output_path: Optional[str | Path] = None) -> Optional[Path]:
    """
    Export drawio_path → PNG.

    drawio_path:  path to the .drawio file
    output_path:  desired PNG path; defaults to same directory with .png extension

    Returns the output Path on success, or None if the CLI is not available.
    """
    if not DRAWIO_CLI:
        logger.warning("draw.io CLI not found — PNG export skipped.")
        return None

    drawio_path = Path(drawio_path)
    if output_path is None:
        output_path = drawio_path.with_suffix(".png")
    output_path = Path(output_path)

    cmd = [
        "xvfb-run", "--auto-servernum", "--",
        DRAWIO_CLI,
        "--export",
        "--format", "png",
        "--output", str(output_path),
        str(drawio_path),
    ]

    logger.info("Exporting PNG: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            logger.error("draw.io CLI error: %s", result.stderr)
            return None
        logger.info("PNG written: %s", output_path)
        return output_path
    except subprocess.TimeoutExpired:
        logger.error("draw.io CLI timed out after 60s")
        return None
    except Exception as e:
        logger.error("PNG export failed: %s", e)
        return None
