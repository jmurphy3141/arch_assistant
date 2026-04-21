from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


@dataclass
class PromptQualityRow:
    path_id: str
    agent: str
    stage: str
    check_type: str
    status: str
    evidence: str
    score: float | None = None


def _artifact_dir() -> Path:
    base = os.environ.get("PROMPT_QUALITY_REPORT_DIR", "")
    if base.strip():
        path = Path(base).expanduser().resolve()
    else:
        path = Path("/tmp/prompt-quality")
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_report(rows: Iterable[PromptQualityRow], report_name: str) -> Path:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": "1",
        "rows": [asdict(row) for row in rows],
    }
    out_path = _artifact_dir() / report_name
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out_path
