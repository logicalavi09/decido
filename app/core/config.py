from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    db_path: str = field(default_factory=lambda: str(PROJECT_ROOT / "data" / "workflow.db"))
    workflows_dir: str = field(default_factory=lambda: str(PROJECT_ROOT / "workflows"))
    scheduler_enabled: bool = True
    scheduler_interval_seconds: float = 1.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_path=os.getenv("WORKFLOW_DB_PATH", str(PROJECT_ROOT / "data" / "workflow.db")),
            workflows_dir=os.getenv("WORKFLOW_CONFIG_DIR", str(PROJECT_ROOT / "workflows")),
            scheduler_enabled=os.getenv("SCHEDULER_ENABLED", "true").lower()
            not in {"0", "false", "no"},
            scheduler_interval_seconds=float(os.getenv("SCHEDULER_INTERVAL_SECONDS", "1.0")),
        )
