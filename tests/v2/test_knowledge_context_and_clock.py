from datetime import date

import pytest
from pydantic import ValidationError

from app.errors import EmployeeNotFoundError
from app.identity import AuthenticatedEmployeeContext
from app.knowledge.applicability import resolve_knowledge_applicability
from app.knowledge.clock import MELBOURNE_TIMEZONE
from app.knowledge.context import KnowledgeApplicabilityContext
from app.knowledge.vocabulary import AudienceGroup, Jurisdiction
from app.repositories.demo import DemoRepository


@pytest.mark.parametrize("employee_id", ["EMP-1001", "EMP-1002"])
def test_demo_employees_resolve_to_trusted_non_manager_applicability(
    employee_id: str,
) -> None:
    context = resolve_knowledge_applicability(
        AuthenticatedEmployeeContext(employee_id=employee_id),
        DemoRepository(),
    )

    assert context.jurisdiction is Jurisdiction.AU_VIC
    assert context.audience_groups == frozenset(
        {
            AudienceGroup.ALL_EMPLOYEES,
            AudienceGroup.MELBOURNE_EMPLOYEES,
        }
    )
    assert AudienceGroup.MANAGERS not in context.audience_groups


def test_unknown_employee_cannot_resolve_applicability() -> None:
    with pytest.raises(EmployeeNotFoundError):
        resolve_knowledge_applicability(
            AuthenticatedEmployeeContext(employee_id="EMP-9999"),
            DemoRepository(),
        )


def test_applicability_context_is_strict_and_immutable() -> None:
    context = KnowledgeApplicabilityContext(
        jurisdiction=Jurisdiction.AU_VIC,
        audience_groups=frozenset({AudienceGroup.ALL_EMPLOYEES}),
    )

    with pytest.raises(ValidationError):
        context.jurisdiction = Jurisdiction.AU_NSW
    with pytest.raises(ValidationError):
        KnowledgeApplicabilityContext(
            jurisdiction=Jurisdiction.AU_VIC,
            audience_groups=frozenset(),
        )


def test_melbourne_timezone_and_fixed_test_date_are_explicit() -> None:
    fixed_today = date(2026, 8, 25)

    assert MELBOURNE_TIMEZONE.key == "Australia/Melbourne"
    assert fixed_today.isoformat() == "2026-08-25"
