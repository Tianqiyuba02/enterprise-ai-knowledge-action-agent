"""Controlled V2 authority and applicability vocabulary."""

from enum import StrEnum


class Jurisdiction(StrEnum):
    GLOBAL = "GLOBAL"
    AU_VIC = "AU-VIC"
    AU_NSW = "AU-NSW"


class AudienceGroup(StrEnum):
    ALL_EMPLOYEES = "all_employees"
    MELBOURNE_EMPLOYEES = "melbourne_employees"
    MANAGERS = "managers"
