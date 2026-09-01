import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REMOVED_MODULES = (
    "app.workflow.checkpointing",
    "app.workflow.orchestration",
    "app.workflow.graph",
    "app.workflow.worker",
    "app.workflow.execution",
    "app.workflow.executor",
    "app.workflow.finalization",
    "app.workflow.execution_repository",
    "app.workflow.outbox_repository",
    "app.workflow.runtime",
)
EXECUTION_PATH_FILES = (
    ROOT / "src" / "app" / "workflow" / "atomic_execution.py",
    ROOT / "src" / "app" / "workflow" / "confirmed_poller.py",
    ROOT / "src" / "app" / "workflow" / "action_creation.py",
    ROOT / "src" / "app" / "workflow" / "confirmation.py",
)
FORBIDDEN_EXECUTION_MARKERS = (
    "PostgresSaver",
    "from langgraph",
    "import langgraph",
    "workflow_outbox",
    "action_execution_ledger",
    "ExecutionPermit",
    "LeaveSubmissionExecutor",
    "WorkflowOrchestrationService",
    "class WorkflowWorker",
)


def test_retired_execution_modules_cannot_be_imported() -> None:
    for name in REMOVED_MODULES:
        try:
            importlib.import_module(name)
        except ModuleNotFoundError:
            continue
        raise AssertionError(f"{name} must not remain importable")


def test_project_dependencies_do_not_include_langgraph() -> None:
    requirements = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "langgraph" not in requirements
    assert "langgraph-checkpoint-postgres" not in requirements
    assert '"langchain"' not in requirements


def test_final_action_execution_path_has_no_legacy_infrastructure() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in EXECUTION_PATH_FILES)
    lowered = combined.lower()
    for marker in FORBIDDEN_EXECUTION_MARKERS:
        assert marker.lower() not in lowered


def test_poller_cli_uses_confirmed_action_poller() -> None:
    source = (ROOT / "src" / "app" / "workflow" / "worker_cli.py").read_text(encoding="utf-8")
    assert "ConfirmedActionPoller" in source
    assert "WorkflowWorker" not in source
    assert "langgraph" not in source.lower()
