from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import SimulationConfig


class RunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow: str = Field(min_length=1)
    input: dict[str, Any]
    simulation: SimulationConfig | None = None


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    simulation: SimulationConfig | None = None


class ImpactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: dict[str, Any]
    sample_size: int = Field(default=50, ge=1, le=500)
    inputs: list[dict[str, Any]] | None = None
    simulation: SimulationConfig | None = None
    include_inputs: bool = False


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    reviewer: str = Field(min_length=1)
    comment: str = ""
