from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_DIR = PROJECT_ROOT / "workflows"


@pytest.fixture
def workflows_dir(tmp_path: Path) -> Path:
    destination = tmp_path / "workflows"
    shutil.copytree(WORKFLOWS_DIR, destination)
    return destination


@pytest.fixture
def app(tmp_path: Path, workflows_dir: Path):
    return create_app(
        Settings(
            db_path=str(tmp_path / "test.db"),
            workflows_dir=str(workflows_dir),
            scheduler_enabled=False,
        )
    )


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client
