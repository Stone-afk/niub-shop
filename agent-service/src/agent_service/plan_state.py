"""Strict, model-owned planning state for one Agent run."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


PlanStatus = Literal["draft", "in_progress", "completed", "blocked", "cancelled"]
StepStatus = Literal[
    "pending", "in_progress", "completed", "blocked", "skipped"
]


class PlanStep(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
    ]
    status: StepStatus
    depends_on: list[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    ] = Field(default_factory=list)


class PlanState(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    plan_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    goal: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]
    status: PlanStatus
    current_step_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ] | None = None
    steps: list[PlanStep] = Field(min_length=1, max_length=64)
    next_action: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]
    reason: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]

    @model_validator(mode="after")
    def validate_step_references(self) -> "PlanState":
        step_ids = [step.id for step in self.steps]
        if len(set(step_ids)) != len(step_ids):
            raise ValueError("plan step IDs must be unique")
        if self.current_step_id is not None and self.current_step_id not in step_ids:
            raise ValueError("current_step_id must reference a plan step")
        if any(dependency not in step_ids for step in self.steps for dependency in step.depends_on):
            raise ValueError("step dependencies must reference plan steps")
        return self


def validate_plan_state(payload: object) -> PlanState:
    """Validate an untrusted model payload into an immutable Plan State."""

    return PlanState.model_validate(payload, strict=True)
