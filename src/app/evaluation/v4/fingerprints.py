"""Deterministic V4 evaluation fingerprints. Secrets are never included."""

import hashlib
import json
from pathlib import Path

from app.agent.client import AGENT_SYSTEM_INSTRUCTION
from app.agent.contracts import V3_TOOL_ALLOWLIST
from app.evaluation.v4.models import V4EvaluationFingerprints

_PRODUCT_PATHS = (
    Path("src/app/workflow/action_creation.py"),
    Path("src/app/api/assistant_application.py"),
    Path("src/app/api/assistant_models.py"),
    Path("src/app/evaluation/v4/models.py"),
    Path("src/app/evaluation/v4/metrics.py"),
    Path("src/app/evaluation/v4/runner.py"),
)


def sha256_json(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def agent_policy_fingerprint() -> str:
    canonical = {
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
    return sha256_json(canonical)


def product_code_fingerprint(*, root: Path | None = None) -> str:
    base = root or Path.cwd()
    parts: list[dict[str, str]] = []
    for relative in _PRODUCT_PATHS:
        path = base / relative
        parts.append({"path": relative.as_posix(), "sha256": sha256_text(path.read_text())})
    return sha256_json(parts)


def build_fingerprints(
    development_set: str,
    *,
    root: Path | None = None,
) -> V4EvaluationFingerprints:
    return V4EvaluationFingerprints(
        development_set=development_set,
        agent_policy=agent_policy_fingerprint(),
        product_code=product_code_fingerprint(root=root),
    )
