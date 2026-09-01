from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_leave_persist_has_exactly_one_runtime_caller() -> None:
    atomic = (ROOT / "src" / "app" / "workflow" / "atomic_execution.py").read_text(encoding="utf-8")
    assert atomic.count("self._leave_commands.persist(") == 1
    assert "def reconcile(" not in atomic
    assert "LeaveSubmissionExecutor" not in atomic


def test_worker_identity_is_owned_by_the_poller_process() -> None:
    poller = (ROOT / "src" / "app" / "workflow" / "confirmed_poller.py").read_text(encoding="utf-8")
    atomic = (ROOT / "src" / "app" / "workflow" / "atomic_execution.py").read_text(encoding="utf-8")
    assert "worker_id=" in poller
    assert 'payload.get("worker_id")' not in atomic
    assert "claimed.worker_id" not in poller
