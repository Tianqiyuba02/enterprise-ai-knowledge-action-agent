"""Deterministic server-owned employee-to-knowledge applicability mapping."""

from app.errors import EmployeeNotFoundError
from app.identity import AuthenticatedEmployeeContext
from app.knowledge.context import KnowledgeApplicabilityContext
from app.knowledge.vocabulary import AudienceGroup, Jurisdiction
from app.repositories.demo import DemoRepository


class ApplicabilityContextError(RuntimeError):
    """Raised when trusted fixture data cannot resolve a supported applicability context."""


def resolve_knowledge_applicability(
    identity: AuthenticatedEmployeeContext,
    repository: DemoRepository,
) -> KnowledgeApplicabilityContext:
    """Resolve applicability only from the authenticated employee's server-owned record."""

    employee = repository.get_employee(identity.employee_id)
    if employee is None:
        raise EmployeeNotFoundError
    if not employee.is_active:
        raise ApplicabilityContextError("Inactive employees have no knowledge applicability.")
    if employee.location != "Melbourne":
        raise ApplicabilityContextError("The employee location has no configured jurisdiction.")
    return KnowledgeApplicabilityContext(
        jurisdiction=Jurisdiction.AU_VIC,
        audience_groups=frozenset(
            {
                AudienceGroup.ALL_EMPLOYEES,
                AudienceGroup.MELBOURNE_EMPLOYEES,
            }
        ),
    )
