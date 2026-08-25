"""Deterministic provider declarations generated only from the trusted registry."""

from google.genai import types

from app.agent.contracts import V3_TOOL_ALLOWLIST


def build_provider_function_declarations() -> tuple[types.FunctionDeclaration, ...]:
    """Build one stable declaration per approved registry contract."""

    return tuple(
        types.FunctionDeclaration(
            name=contract.name.value,
            description=contract.description,
            parameters_json_schema=contract.argument_model.model_json_schema(),
        )
        for contract in V3_TOOL_ALLOWLIST.values()
    )
