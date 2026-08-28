from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(*parts: str) -> str:
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def test_leave_submit_has_exactly_one_normal_runtime_caller() -> None:
    runtime = _read("src", "app", "workflow", "runtime.py")
    executor = _read("src", "app", "workflow", "executor.py")
    finalization = _read("src", "app", "workflow", "finalization.py")
    orchestration = _read("src", "app", "workflow", "orchestration.py")
    worker = _read("src", "app", "workflow", "worker.py")
    graph = _read("src", "app", "workflow", "graph.py")

    assert runtime.count("self._executor.submit(") == 1
    assert "def execute(" in runtime
    execute_block = runtime.split("def execute(", 1)[1].split("def ", 1)[0]
    assert "self._executor.submit(" in execute_block
    finalize_block = runtime.split("def finalize(", 1)[1].split("def ", 1)[0]
    assert "self._executor.submit(" not in finalize_block
    reconcile_block = runtime.split("def reconcile(", 1)[1].split("def ", 1)[0]
    assert "self._executor.submit(" not in reconcile_block

    assert "LeaveSubmissionExecutor" not in finalization
    assert ".submit(" not in finalization
    assert "_advance_execution" not in orchestration
    assert "runtime.execute(" not in orchestration
    assert "LeaveSubmissionExecutor" not in orchestration
    assert "LeaveSubmissionExecutor" not in worker
    assert ".submit(" not in worker
    assert "LeaveSubmissionExecutor" not in graph
    assert ".submit(" not in graph

    assert "def reconcile(" not in executor
    assert "conclude_absence" not in executor
    assert "def begin_reconciliation(" not in finalization
    persist = executor.find("self._leave_commands.persist(")
    assert persist > 0
    assert executor.count("self._leave_commands.persist(") == 1


def test_worker_identity_is_not_taken_from_payloads() -> None:
    runtime = _read("src", "app", "workflow", "runtime.py")
    orchestration = _read("src", "app", "workflow", "orchestration.py")
    worker = _read("src", "app", "workflow", "worker.py")
    assert "worker_id=self.worker_id" in runtime
    assert 'resume_payload={"wake": True}' in orchestration
    assert "worker_id=self.worker_id" in worker
    assert "claimed.worker_id" not in worker
    assert 'payload.get("worker_id")' not in orchestration
    assert "payload.get('worker_id')" not in orchestration
