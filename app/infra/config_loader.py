from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.core.clock import to_iso
from app.core.errors import ConfigError, WorkflowNotFoundError
from app.domain.models import WorkflowConfig


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def compute_config_etag(workflows: dict[str, WorkflowConfig]) -> str:
    body = {name: workflow.snapshot() for name, workflow in sorted(workflows.items())}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


class WorkflowRegistry:
    """Loads and validates workflow definitions from a directory of YAML files.

    Reloads are atomic: if any file is invalid, the previous registry is kept
    intact so in-flight traffic never sees a half-loaded configuration set.
    """

    def __init__(self, directory: str) -> None:
        self.directory = Path(directory)
        self._workflows: dict[str, WorkflowConfig] = {}
        self._sources: dict[str, str] = {}
        self.loaded_at: str | None = None

    def _read_directory(self) -> tuple[dict[str, WorkflowConfig], dict[str, str], list[dict[str, str]]]:
        loaded: dict[str, WorkflowConfig] = {}
        sources: dict[str, str] = {}
        errors: list[dict[str, str]] = []

        if not self.directory.exists():
            raise ConfigError(f"workflow directory not found: {self.directory}")

        files = sorted(
            [*self.directory.glob("*.yaml"), *self.directory.glob("*.yml")]
        )
        if not files:
            raise ConfigError(f"no workflow YAML files found in {self.directory}")

        for path in files:
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("workflow file must contain a YAML mapping")
                workflow = WorkflowConfig.model_validate(raw)
            except ValidationError as exc:
                errors.append(
                    {
                        "file": path.name,
                        "error": "; ".join(
                            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                            for error in exc.errors()
                        ),
                    }
                )
                continue
            except Exception as exc:  # noqa: BLE001 - surfaced as config detail
                errors.append({"file": path.name, "error": str(exc)})
                continue

            if workflow.name in loaded:
                errors.append(
                    {
                        "file": path.name,
                        "error": f"duplicate workflow name '{workflow.name}'",
                    }
                )
                continue
            loaded[workflow.name] = workflow
            sources[workflow.name] = path.name

        return loaded, sources, errors

    def load(self) -> dict[str, WorkflowConfig]:
        loaded, sources, errors = self._read_directory()
        if errors:
            raise ConfigError("workflow configuration is invalid", details=errors)
        self._workflows = loaded
        self._sources = sources
        self.loaded_at = to_iso()
        return dict(self._workflows)

    def reload(self) -> dict[str, WorkflowConfig]:
        return self.load()

    def preview(self) -> tuple[dict[str, WorkflowConfig], dict[str, str]]:
        """Validate and return candidate configuration without activating it."""

        loaded, sources, errors = self._read_directory()
        if errors:
            raise ConfigError("workflow configuration is invalid", details=errors)
        return loaded, sources

    def get(self, name: str) -> WorkflowConfig:
        workflow = self._workflows.get(name)
        if workflow is None:
            raise WorkflowNotFoundError(
                f"workflow '{name}' is not registered",
                details={"available": sorted(self._workflows)},
            )
        return workflow

    def all(self) -> list[WorkflowConfig]:
        return sorted(self._workflows.values(), key=lambda item: item.name)

    def sources(self) -> dict[str, str]:
        return dict(self._sources)

    def etag(self) -> str:
        return compute_config_etag(self._workflows)

    @staticmethod
    def summarize(
        workflows: dict[str, WorkflowConfig],
        sources: dict[str, str],
        *,
        loaded_at: str | None = None,
        etag: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "loaded_at": loaded_at,
            "count": len(workflows),
            "workflows": [
                {
                    "name": workflow.name,
                    "version": workflow.version,
                    "description": workflow.description,
                    "stages": workflow.stage_names(),
                    "source": sources.get(workflow.name),
                }
                for workflow in sorted(workflows.values(), key=lambda item: item.name)
            ],
        }
        if etag is not None:
            payload["config_etag"] = etag
        return payload

    def summary(self) -> dict[str, Any]:
        return self.summarize(
            self._workflows,
            self._sources,
            loaded_at=self.loaded_at,
            etag=self.etag(),
        )
