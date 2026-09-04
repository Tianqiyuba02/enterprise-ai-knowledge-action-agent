"""Internal M3 readiness projection consumed by the public Next.js BFF."""

from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import (
    get_authenticated_employee,
    get_demo_control_service,
    get_demo_repository,
    get_workflow_session_factory,
)
from app.demo.calendar import DemoHolidayCalendarService
from app.demo.service import DemoControlService
from app.identity import AuthenticatedEmployeeContext
from app.knowledge.clock import MelbourneClock
from app.repositories.demo import DemoRepository

router = APIRouter(prefix="/demo", tags=["demo"])


class DemoReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "degraded", "maintenance"]
    database: bool
    migration: bool
    knowledge: bool
    maintenance: bool
    worker: bool
    worker_heartbeat_at: datetime | None
    last_successful_reset_at: datetime | None
    document_count: int
    chunk_count: int


class GuidedScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    prompt: str
    available: bool = True
    note: str | None = None


class GuidedScenariosResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[GuidedScenario]


@router.get("/readiness", response_model=DemoReadinessResponse)
def demo_readiness(
    service: Annotated[DemoControlService, Depends(get_demo_control_service)],
) -> DemoReadinessResponse:
    return DemoReadinessResponse.model_validate(service.readiness(), from_attributes=True)


@router.get("/guided-scenarios", response_model=GuidedScenariosResponse)
def guided_scenarios(
    context: Annotated[AuthenticatedEmployeeContext, Depends(get_authenticated_employee)],
    repository: Annotated[DemoRepository, Depends(get_demo_repository)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_workflow_session_factory)],
) -> GuidedScenariosResponse:
    """Preflight the relative leave demo before presenting its executable prompt."""

    today = MelbourneClock().today()
    next_friday = today + timedelta(days=(4 - today.weekday()) % 7 or 7)
    profile = repository.get_employee(context.employee_id)
    selected: date | None = None
    note: str | None = None
    calendar = DemoHolidayCalendarService()
    candidates = [next_friday + timedelta(days=offset) for offset in range(61)]
    with session_factory() as session:
        for candidate in candidates:
            if candidate.strftime("%A").lower() not in profile.work_days:
                continue
            result = calendar.holidays_for_range(
                session,
                jurisdiction=context.jurisdiction or "AU-VIC",
                start_date=candidate,
                end_date=candidate,
            )
            if result.covered and not result.holidays:
                selected = candidate
                break
    if selected and selected != next_friday:
        note = (
            "Next Friday is not a scheduled working day for this profile; "
            "a valid alternative is used."
        )
    leave_prompt = (
        f"Prepare annual leave for {selected.isoformat()}."
        if selected
        else "Explain why the demo calendar cannot safely prepare next Friday."
    )
    return GuidedScenariosResponse(
        items=[
            GuidedScenario(
                id="carry-over",
                label="Carry over leave",
                prompt="Can I carry over unused annual leave? Cite the applicable policy.",
            ),
            GuidedScenario(
                id="next-friday",
                label="Book next Friday",
                prompt=leave_prompt,
                available=selected is not None,
                note=note,
            ),
            GuidedScenario(
                id="broken-laptop",
                label="Broken laptop",
                prompt=(
                    "My laptop is broken and I cannot work. Prepare a high-urgency hardware "
                    "IT support request."
                ),
            ),
        ]
    )
