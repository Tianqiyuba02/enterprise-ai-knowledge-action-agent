"""Authenticated current-employee read endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.dependencies import (
    get_authenticated_employee,
    get_employee_service,
    get_it_service,
    get_portal_read_service,
)
from app.api.models import (
    EmployeeProfileResponse,
    ErrorResponse,
    LeaveBalanceResponse,
    LeaveBalancesResponse,
    TicketResponse,
)
from app.api.portal_models import ActionListResponse, LeaveSummaryResponse
from app.identity import AuthenticatedEmployeeContext
from app.portal.service import PortalReadService
from app.services.employee import EmployeeService
from app.services.it import ITService

router = APIRouter(prefix="/me", tags=["me"])

AUTH_RESPONSES = {
    401: {"model": ErrorResponse, "description": "Invalid demo session"},
    404: {"model": ErrorResponse, "description": "Owned resource not found"},
    422: {"model": ErrorResponse, "description": "Invalid request"},
}


@router.get(
    "/profile",
    response_model=EmployeeProfileResponse,
    responses=AUTH_RESPONSES,
)
def get_my_profile(
    context: Annotated[AuthenticatedEmployeeContext, Depends(get_authenticated_employee)],
    service: Annotated[EmployeeService, Depends(get_employee_service)],
) -> EmployeeProfileResponse:
    return EmployeeProfileResponse.model_validate(service.get_my_profile(context))


@router.get(
    "/leave/balances",
    response_model=LeaveBalancesResponse,
    responses=AUTH_RESPONSES,
)
def get_my_leave_balances(
    context: Annotated[AuthenticatedEmployeeContext, Depends(get_authenticated_employee)],
    service: Annotated[EmployeeService, Depends(get_employee_service)],
) -> LeaveBalancesResponse:
    balances = service.get_my_leave_balances(context)
    return LeaveBalancesResponse(
        balances=tuple(LeaveBalanceResponse.model_validate(balance) for balance in balances)
    )


@router.get(
    "/leave/summary",
    response_model=LeaveSummaryResponse,
    responses={
        **AUTH_RESPONSES,
        503: {"model": ErrorResponse, "description": "Portal read unavailable"},
    },
)
def get_my_leave_summary(
    context: Annotated[AuthenticatedEmployeeContext, Depends(get_authenticated_employee)],
    service: Annotated[PortalReadService, Depends(get_portal_read_service)],
) -> LeaveSummaryResponse:
    return service.leave_summary(context)


@router.get(
    "/actions",
    response_model=ActionListResponse,
    responses={
        **AUTH_RESPONSES,
        503: {"model": ErrorResponse, "description": "Portal read unavailable"},
    },
)
def list_my_actions(
    context: Annotated[AuthenticatedEmployeeContext, Depends(get_authenticated_employee)],
    service: Annotated[PortalReadService, Depends(get_portal_read_service)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ActionListResponse:
    return service.list_actions(context, limit=limit)


@router.get(
    "/tickets/{ticket_id}",
    response_model=TicketResponse,
    responses=AUTH_RESPONSES,
)
def get_my_ticket(
    ticket_id: Annotated[str, Path(pattern=r"^TKT-\d{4}$")],
    context: Annotated[AuthenticatedEmployeeContext, Depends(get_authenticated_employee)],
    service: Annotated[ITService, Depends(get_it_service)],
) -> TicketResponse:
    return TicketResponse.model_validate(service.get_my_ticket(ticket_id, context))
