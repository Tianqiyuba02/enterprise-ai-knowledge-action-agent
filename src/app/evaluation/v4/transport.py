"""Evaluator-transport policy for v4-product-eval-2. Not product behavior."""

from pathlib import Path
from typing import Final

from app.agent.provider_failures import AgentProviderFailureKind

V4_EVALUATOR_VERSION: Final = "v4-product-eval-2"
V4_REPORT_VERSION: Final = "v4-product-eval-2"
V4_DEVELOPMENT_SET_VERSION: Final = "v4-product-dev-1"

CIRCUIT_BREAKER_CONSECUTIVE_THRESHOLD: Final = 2
CIRCUIT_BREAKER_CATEGORIES: Final = (AgentProviderFailureKind.RATE_LIMITED.value,)
DEFAULT_CASE_DELAY_SECONDS: Final = 0.0

PREFLIGHT_SCORED: Final = False
PREFLIGHT_CREATES_V4_ACTION: Final = False
PREFLIGHT_IS_DEVELOPMENT_CASE: Final = False
PREFLIGHT_IS_HOLDOUT_CASE: Final = False

RUN1_EVALUATOR_VERSION: Final = "v4-product-eval-1"
RUN1_STATUS: Final = "PARTIAL / PROVIDER-LIMITED"
RUN1_EVIDENCE_COMMIT: Final = "d2092d367504eb6c9e83e0c212015641335ba1e6"
RUN1_PROVIDER_COMPLETED: Final = 9
RUN1_PROVIDER_BLOCKED: Final = 7
RUN1_SEMANTIC_PASS_AMONG_EVALUABLE: Final = "9/9"

DEFAULT_EVAL2_OUTPUT: Final = "evals/results/v4-product-development-eval-2.json"
DEFAULT_PREFLIGHT_OUTPUT: Final = "evals/results/v4-provider-preflight.json"
DEFAULT_PREFLIGHT_RESULTS_DIR: Final = Path("evals/results")
LAUNCH_PREFLIGHT_FILENAME_PREFIX: Final = "v4-provider-preflight-launch-"
DIAGNOSTIC_PAIR_FILENAME_PREFIX: Final = "v4-provider-diagnostic-pair-"
DIAGNOSTIC_PAIR_ORDER: Final = ("agent_shaped", "minimal_control")
DIAGNOSTIC_SCORED: Final = False
STANDALONE_PREFLIGHT_20260828_PATH: Final = DEFAULT_PREFLIGHT_OUTPUT
STANDALONE_PREFLIGHT_20260829_PATH: Final = "evals/results/v4-provider-preflight-2026-08-29.json"
RUN1_LIVE_PATH: Final = "evals/results/v4-product-development.json"
RUN1_ARCHIVE_PATH: Final = "evals/results/archive/v4-product-dev-1-eval-1-run-1.json"
RUN1_IDENTITY_PATH: Final = "evals/results/archive/v4-product-dev-1-eval-1-run-1.identity.json"
RESERVED_PREFLIGHT_EVIDENCE_PATHS: Final = (
    Path(STANDALONE_PREFLIGHT_20260828_PATH),
    Path(STANDALONE_PREFLIGHT_20260829_PATH),
    Path(DEFAULT_EVAL2_OUTPUT),
    Path(RUN1_LIVE_PATH),
    Path(RUN1_ARCHIVE_PATH),
    Path(RUN1_IDENTITY_PATH),
)
RESERVED_EVIDENCE_NAME_PREFIXES: Final = (LAUNCH_PREFLIGHT_FILENAME_PREFIX,)


def transport_policy_payload() -> dict[str, object]:
    return {
        "evaluator_version": V4_EVALUATOR_VERSION,
        "circuit_breaker": {
            "consecutive_threshold": CIRCUIT_BREAKER_CONSECUTIVE_THRESHOLD,
            "categories": list(CIRCUIT_BREAKER_CATEGORIES),
            "adaptive": False,
        },
        "preflight": {
            "scored": PREFLIGHT_SCORED,
            "creates_v4_action": PREFLIGHT_CREATES_V4_ACTION,
            "development_case": PREFLIGHT_IS_DEVELOPMENT_CASE,
            "holdout_case": PREFLIGHT_IS_HOLDOUT_CASE,
            "required_before_new_development_run": True,
            "launch_result_persisted": True,
            "launch_artifact_separate_from_development_result": True,
        },
        "provider_diagnostic_pair": {
            "scored": DIAGNOSTIC_SCORED,
            "development_case": PREFLIGHT_IS_DEVELOPMENT_CASE,
            "holdout_case": PREFLIGHT_IS_HOLDOUT_CASE,
            "creates_v4_action": PREFLIGHT_CREATES_V4_ACTION,
            "order": list(DIAGNOSTIC_PAIR_ORDER),
            "provider_calls": 2,
            "retries": False,
            "tool_execution": False,
        },
        "attempt_history": "structured_safe_diagnostics",
        "default_delay_seconds": DEFAULT_CASE_DELAY_SECONDS,
        "batch_api": False,
    }
