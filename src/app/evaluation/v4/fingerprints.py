"""Deterministic V4 evaluation fingerprints. Secrets are never included."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Final

from sqlalchemy import Engine, text

from app.agent.client import AGENT_SYSTEM_INSTRUCTION
from app.agent.contracts import V3_TOOL_ALLOWLIST
from app.config import AgentSettings
from app.evaluation.v4.clock import business_clock_identity
from app.evaluation.v4.models import V4EvaluationFingerprints
from app.evaluation.v4.transport import transport_policy_payload
from app.repositories.demo import DemoRepository

PROVIDER_THINKING_LEVEL: Final = "MINIMAL"
PROVIDER_TEMPERATURE: Final = 0
PROVIDER_MAX_OUTPUT_TOKENS: Final = 1_024
PROVIDER_AUTOMATIC_FUNCTION_CALLING: Final = False

_SUBJECT_PATHS: Final = (
    Path("src/app/agent/client.py"),
    Path("src/app/agent/contracts.py"),
    Path("src/app/agent/dispatcher.py"),
    Path("src/app/agent/errors.py"),
    Path("src/app/agent/leave_models.py"),
    Path("src/app/agent/loop_models.py"),
    Path("src/app/agent/models.py"),
    Path("src/app/agent/provider.py"),
    Path("src/app/agent/provider_failures.py"),
    Path("src/app/agent/relative_weekday.py"),
    Path("src/app/agent/service.py"),
    Path("src/app/api/assistant_application.py"),
    Path("src/app/api/assistant_models.py"),
    Path("src/app/evaluation/v4/clock.py"),
    Path("src/app/knowledge/applicability.py"),
    Path("src/app/knowledge/citations.py"),
    Path("src/app/knowledge/context.py"),
    Path("src/app/knowledge/models.py"),
    Path("src/app/knowledge/query_service.py"),
    Path("src/app/knowledge/repository.py"),
    Path("src/app/knowledge/service.py"),
    Path("src/app/knowledge/vocabulary.py"),
    Path("src/app/repositories/demo.py"),
    Path("src/app/services/employee.py"),
    Path("src/app/services/it.py"),
    Path("src/app/services/leave_preparation.py"),
    Path("src/app/workflow/action_creation.py"),
    Path("src/app/workflow/authority.py"),
    Path("src/app/workflow/balance.py"),
    Path("src/app/workflow/calendar.py"),
    Path("src/app/workflow/calendar_service.py"),
    Path("src/app/workflow/canonical.py"),
    Path("src/app/workflow/confirmation.py"),
    Path("src/app/workflow/domain.py"),
    Path("src/app/workflow/executable_preparation.py"),
    Path("src/app/workflow/execution.py"),
    Path("src/app/workflow/executor.py"),
    Path("src/app/workflow/finalization.py"),
    Path("src/app/workflow/graph.py"),
    Path("src/app/workflow/holiday_repository.py"),
    Path("src/app/workflow/orchestration.py"),
    Path("src/app/workflow/worker.py"),
)

_TRANSPORT_PATHS: Final = (
    Path("src/app/evaluation/v4/cli.py"),
    Path("src/app/evaluation/v4/diagnostic.py"),
    Path("src/app/evaluation/v4/fingerprints.py"),
    Path("src/app/evaluation/v4/metrics.py"),
    Path("src/app/evaluation/v4/models.py"),
    Path("src/app/evaluation/v4/preflight.py"),
    Path("src/app/evaluation/v4/run1_archive.py"),
    Path("src/app/evaluation/v4/runner.py"),
    Path("src/app/evaluation/v4/transport.py"),
)


def sha256_json(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def digest_embedding(values: object) -> str:
    if isinstance(values, str):
        stripped = values.strip()
        if not (stripped.startswith("[") and stripped.endswith("]")):
            raise ValueError("unsupported embedding text representation")
        inner = stripped[1:-1].strip()
        floats = [float(part) for part in inner.split(",")] if inner else []
    else:
        floats = [float(item) for item in values]
    packed = struct.pack("<" + "d" * len(floats), *floats)
    return hashlib.sha256(packed).hexdigest()


def agent_policy_canonical() -> dict[str, object]:
    return {
        "system_instruction": AGENT_SYSTEM_INSTRUCTION,
        "tools": [
            {
                "name": name.value,
                "capability": contract.capability.value,
                "arguments": contract.argument_model.model_json_schema(),
            }
            for name, contract in V3_TOOL_ALLOWLIST.items()
        ],
    }


def evaluation_subject_payload(*, root: Path | None = None) -> dict[str, object]:
    base = root or Path.cwd()
    sources = [
        {"path": relative.as_posix(), "sha256": sha256_text((base / relative).read_text())}
        for relative in _SUBJECT_PATHS
    ]
    return {"agent_policy": agent_policy_canonical(), "sources": sources}


def evaluation_subject_fingerprint(*, root: Path | None = None) -> str:
    return sha256_json(evaluation_subject_payload(root=root))


def evaluation_transport_payload(*, root: Path | None = None) -> dict[str, object]:
    base = root or Path.cwd()
    sources = [
        {"path": relative.as_posix(), "sha256": sha256_text((base / relative).read_text())}
        for relative in _TRANSPORT_PATHS
    ]
    return {"policy": transport_policy_payload(), "sources": sources}


def evaluation_transport_fingerprint(*, root: Path | None = None) -> str:
    return sha256_json(evaluation_transport_payload(root=root))


def provider_config_payload(settings: AgentSettings | None = None) -> dict[str, object]:
    loaded = settings or AgentSettings()
    return {
        "agent_model": loaded.agent_model,
        "thinking_level": PROVIDER_THINKING_LEVEL,
        "temperature": PROVIDER_TEMPERATURE,
        "max_output_tokens": PROVIDER_MAX_OUTPUT_TOKENS,
        "automatic_function_calling": PROVIDER_AUTOMATIC_FUNCTION_CALLING,
        "timeout_seconds": loaded.agent_timeout_seconds,
        "max_attempts": loaded.agent_max_attempts,
    }


def provider_config_fingerprint(settings: AgentSettings | None = None) -> str:
    return sha256_json(provider_config_payload(settings))


def business_clock_fingerprint() -> str:
    return sha256_json(business_clock_identity())


def demo_fixture_payload(repository: DemoRepository | None = None) -> dict[str, object]:
    return (repository or DemoRepository()).evaluation_fixture_snapshot()


def canonical_baseline_payload(
    *,
    documents: list[dict[str, object]],
    chunks: list[dict[str, object]],
    holidays: list[dict[str, object]],
    demo: dict[str, object],
) -> dict[str, object]:
    return {
        "documents": documents,
        "chunks": chunks,
        "holidays": holidays,
        "demo_fixtures": demo,
    }


def hash_baseline_payload(payload: dict[str, object]) -> str:
    return sha256_json(payload)


def baseline_data_fingerprint(
    engine: Engine,
    *,
    repository: DemoRepository | None = None,
) -> str:
    with engine.connect() as connection:
        documents = [
            {
                "id": str(row["id"]),
                "doc_code": row["doc_code"],
                "version": row["version"],
                "title": row["title"],
                "status": row["status"],
                "effective_date": str(row["effective_date"]),
                "expiry_date": None if row["expiry_date"] is None else str(row["expiry_date"]),
                "jurisdiction": row["jurisdiction"],
                "audience_groups": list(row["audience_groups"]),
                "source_uri": row["source_uri"],
                "content_checksum": row["content_checksum"],
                "superseded_by_id": (
                    None if row["superseded_by_id"] is None else str(row["superseded_by_id"])
                ),
                "embedding_model_id": row["embedding_model_id"],
                "embedding_dimension": int(row["embedding_dimension"]),
            }
            for row in connection.execute(
                text(
                    """
                    SELECT id, doc_code, version, title, status, effective_date, expiry_date,
                           jurisdiction, audience_groups, source_uri, content_checksum,
                           superseded_by_id, embedding_model_id, embedding_dimension
                    FROM documents
                    ORDER BY id
                    """
                )
            ).mappings()
        ]
        chunks = [
            {
                "id": str(row["id"]),
                "document_id": str(row["document_id"]),
                "chunk_index": int(row["chunk_index"]),
                "section_label": row["section_label"],
                "anchor": row["anchor"],
                "page": row["page"],
                "content": row["content"],
                "token_count": int(row["token_count"]),
                "embedding_digest": digest_embedding(row["embedding"]),
            }
            for row in connection.execute(
                text(
                    """
                    SELECT id, document_id, chunk_index, section_label, anchor, page,
                           content, token_count, embedding
                    FROM document_chunks
                    ORDER BY id
                    """
                )
            ).mappings()
        ]
        holidays = [
            {
                "jurisdiction": row["jurisdiction"],
                "holiday_date": str(row["holiday_date"]),
                "holiday_name": row["holiday_name"],
                "calendar_version": row["calendar_version"],
            }
            for row in connection.execute(
                text(
                    """
                    SELECT jurisdiction, holiday_date, holiday_name, calendar_version
                    FROM public_holidays
                    ORDER BY holiday_date, holiday_name
                    """
                )
            ).mappings()
        ]
    return hash_baseline_payload(
        canonical_baseline_payload(
            documents=documents,
            chunks=chunks,
            holidays=holidays,
            demo=demo_fixture_payload(repository),
        )
    )


def build_fingerprints(
    development_set: str,
    *,
    baseline_data: str,
    agent_settings: AgentSettings | None = None,
    root: Path | None = None,
) -> V4EvaluationFingerprints:
    return V4EvaluationFingerprints(
        development_set=development_set,
        development_gold=development_set,
        evaluation_subject=evaluation_subject_fingerprint(root=root),
        evaluation_transport=evaluation_transport_fingerprint(root=root),
        provider_config=provider_config_fingerprint(agent_settings),
        baseline_data=baseline_data,
        business_clock=business_clock_fingerprint(),
    )
