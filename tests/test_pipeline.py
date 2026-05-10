import json
import os

import pytest
from database_manager import DatabaseManager
from pipeline import (
    Pipeline,
    Task,
    _set_active_pipeline,
    log_discovery,
    log_pipeline_event,
    update_task_progress,
)


class TestTask:
    """Task dataclass: name, function, dependencies, retries."""

    def test_creates_task(self):
        def noop(db):
            pass

        t = Task("test", noop, depends_on=["a", "b"], retries=3, retry_delay=10)
        assert t.name == "test"
        assert t.func is noop
        assert t.depends_on == ["a", "b"]
        assert t.retries == 3
        assert t.retry_delay == 10

    def test_defaults(self):
        t = Task("t", lambda db: None)
        assert t.depends_on == []
        assert t.retries == 0
        assert t.retry_delay == 5


class TestPipelineAdd:
    """Task registration via direct call and decorator."""

    def test_direct_add(self):
        p = Pipeline()
        p.add("a", func=lambda db: None)
        assert "a" in p._tasks

    def test_decorator_add(self):
        p = Pipeline()

        @p.add("my_task", depends_on=["x"])
        def my_task(db):
            pass

        assert "my_task" in p._tasks
        assert p._tasks["my_task"].depends_on == ["x"]


class TestPipelineResolveOrder:
    """Kahn topological sort: ordering, filtering, cycle detection."""

    def test_single_task(self):
        p = Pipeline()
        p.add("a", func=lambda db: None)
        assert p._resolve_order() == ["a"]

    def test_independent_tasks(self):
        p = Pipeline()
        p.add("a", func=lambda db: None)
        p.add("b", func=lambda db: None)
        p.add("c", func=lambda db: None)
        order = p._resolve_order()
        assert set(order) == {"a", "b", "c"}
        assert len(order) == 3

    def test_linear_dependency(self):
        p = Pipeline()
        p.add("a", func=lambda db: None)
        p.add("b", func=lambda db: None, depends_on=["a"])
        p.add("c", func=lambda db: None, depends_on=["b"])
        order = p._resolve_order()
        assert order == ["a", "b", "c"]

    def test_diamond_dependency(self):
        p = Pipeline()
        p.add("a", func=lambda db: None)
        p.add("b", func=lambda db: None, depends_on=["a"])
        p.add("c", func=lambda db: None, depends_on=["a"])
        p.add("d", func=lambda db: None, depends_on=["b", "c"])
        order = p._resolve_order()
        assert order[0] == "a"
        assert order[-1] == "d"
        assert order.index("b") > order.index("a")
        assert order.index("c") > order.index("a")
        assert order.index("d") > order.index("b")
        assert order.index("d") > order.index("c")

    def test_task_filter_includes_transitive_deps(self):
        p = Pipeline()
        p.add("a", func=lambda db: None)
        p.add("b", func=lambda db: None, depends_on=["a"])
        p.add("c", func=lambda db: None, depends_on=["b"])
        order = p._resolve_order(task_names=["c"])
        assert order == ["a", "b", "c"]

    def test_task_filter_only_requested_subset(self):
        p = Pipeline()
        p.add("a", func=lambda db: None)
        p.add("b", func=lambda db: None, depends_on=["a"])
        p.add("c", func=lambda db: None)
        order = p._resolve_order(task_names=["b"])
        assert order == ["a", "b"]

    def test_unknown_task_raises(self):
        p = Pipeline()
        with pytest.raises(ValueError, match="not found"):
            p._resolve_order(task_names=["nonexistent"])

    def test_cycle_detection(self):
        p = Pipeline()
        p.add("a", func=lambda db: None, depends_on=["b"])
        p.add("b", func=lambda db: None, depends_on=["a"])
        with pytest.raises(RuntimeError, match="Cycle"):
            p._resolve_order()


class TestPipelineRun:
    """End-to-end pipeline execution: ordering, failures, retries."""

    def test_executes_tasks_in_order(self):
        p = Pipeline()
        executed = []

        p.add("a", func=lambda db: executed.append("a"))
        p.add("b", func=lambda db: executed.append("b"), depends_on=["a"])

        db = DatabaseManager(":memory:")
        results = p.run(db)
        assert results["a"] == "success"
        assert results["b"] == "success"
        assert executed == ["a", "b"]
        db.close()

    def test_dependency_failure_blocks_downstream(self):
        p = Pipeline()

        def fail(db):
            raise ValueError("boom")

        p.add("a", func=fail)
        p.add("b", func=lambda db: None, depends_on=["a"])

        db = DatabaseManager(":memory:")
        results = p.run(db)
        assert results["a"] == "failed"
        assert results["b"] == "skipped"
        db.close()

    def test_retry_succeeds_on_second_attempt(self):
        p = Pipeline()
        attempts = []

        def fail_then_ok(db):
            attempts.append(1)
            if len(attempts) < 2:
                raise ValueError("transient")

        p.add("a", func=fail_then_ok, retries=2, retry_delay=0.01)

        db = DatabaseManager(":memory:")
        results = p.run(db)
        assert results["a"] == "success"
        assert len(attempts) == 2
        db.close()

    def test_retry_exhausted(self):
        p = Pipeline()

        def always_fail(db):
            raise ValueError("persistent")

        p.add("a", func=always_fail, retries=1, retry_delay=0.01)

        db = DatabaseManager(":memory:")
        results = p.run(db)
        assert results["a"] == "failed"
        db.close()

    def test_independent_task_runs_after_failure(self):
        p = Pipeline()

        def fail(db):
            raise ValueError("boom")

        p.add("a", func=fail)
        p.add("b", func=lambda db: None)

        db = DatabaseManager(":memory:")
        results = p.run(db)
        assert results["a"] == "failed"
        assert results["b"] == "success"
        db.close()


class TestLogPipelineEvent:
    """JSONL logging of pipeline task execution events."""

    def test_writes_jsonl_line(self, tmp_path):
        os.environ["DATA_DIR"] = str(tmp_path)
        log_pipeline_event("run1", "task_a", "started")
        log_path = tmp_path / "pipeline_runs.jsonl"
        assert log_path.exists()
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["run_id"] == "run1"
        assert entry["task"] == "task_a"
        assert entry["status"] == "started"

    def test_writes_error(self, tmp_path):
        os.environ["DATA_DIR"] = str(tmp_path)
        log_pipeline_event("run2", "task_b", "failed", duration_s=1.5, error="connection refused")
        log_path = tmp_path / "pipeline_runs.jsonl"
        entry = json.loads(log_path.read_text().strip())
        assert entry["status"] == "failed"
        assert entry["duration_s"] == 1.5
        assert entry["error"] == "connection refused"


class TestLogDiscovery:
    """Schema discovery event logging."""

    def test_writes_new_column_event(self, tmp_path):
        os.environ["DATA_DIR"] = str(tmp_path)
        log_discovery("fixtures", "fixtures", "new_column", column="var_assists", type="INTEGER")
        log_path = tmp_path / "discovery.jsonl"
        entry = json.loads(log_path.read_text().strip())
        assert entry["endpoint"] == "fixtures"
        assert entry["table"] == "fixtures"
        assert entry["event"] == "new_column"
        assert entry["column"] == "var_assists"
        assert entry["type"] == "INTEGER"

    def test_writes_type_change_event(self, tmp_path):
        os.environ["DATA_DIR"] = str(tmp_path)
        log_discovery("bootstrap-static", "elements", "type_change", column="starts", was="INTEGER", now="TEXT")
        log_path = tmp_path / "discovery.jsonl"
        entry = json.loads(log_path.read_text().strip())
        assert entry["event"] == "type_change"
        assert entry["was"] == "INTEGER"
        assert entry["now"] == "TEXT"


class TestUpdateTaskProgress:
    """Progress reporting for long-running parameterized tasks."""

    def test_updates_progress_and_percentage(self, tmp_path):
        os.environ["DATA_DIR"] = str(tmp_path)
        p = Pipeline()
        p.add("a", func=lambda db: None)
        db = DatabaseManager(":memory:")

        p._run_id = "test"
        p._started_at = "2025-01-01T00:00:00Z"
        p._task_states = {"a": {"status": "running"}}
        _set_active_pipeline(p)

        update_task_progress("a", 5, 20)
        assert p._task_states["a"]["current"] == 5
        assert p._task_states["a"]["total"] == 20
        assert p._task_states["a"]["pct"] == 25.0

        _set_active_pipeline(None)
        db.close()

    def test_noop_when_no_active_pipeline(self):
        _set_active_pipeline(None)
        update_task_progress("x", 1, 10)

    def test_noop_when_task_not_in_state(self, tmp_path):
        os.environ["DATA_DIR"] = str(tmp_path)
        p = Pipeline()
        p._run_id = "test"
        p._started_at = "2025-01-01T00:00:00Z"
        p._task_states = {}
        _set_active_pipeline(p)

        update_task_progress("nonexistent", 1, 10)
        _set_active_pipeline(None)

    def test_writes_progress_file(self, tmp_path):
        os.environ["DATA_DIR"] = str(tmp_path)
        p = Pipeline()
        p.add("a", func=lambda db: None)
        db = DatabaseManager(":memory:")

        p._run_id = "test"
        p._started_at = "2025-01-01T00:00:00Z"
        p._task_states = {"a": {"status": "running"}}
        _set_active_pipeline(p)

        update_task_progress("a", 5, 20)
        progress_file = tmp_path / "pipeline_progress.json"
        assert progress_file.exists()
        data = json.loads(progress_file.read_text())
        assert data["run_id"] == "test"
        assert data["tasks"]["a"]["current"] == 5
        assert data["tasks"]["a"]["pct"] == 25.0

        _set_active_pipeline(None)
        db.close()

    def test_current_id_written_to_state_and_file(self, tmp_path):
        os.environ["DATA_DIR"] = str(tmp_path)
        p = Pipeline()
        p.add("a", func=lambda db: None)
        db = DatabaseManager(":memory:")

        p._run_id = "test"
        p._started_at = "2025-01-01T00:00:00Z"
        p._task_states = {"a": {"status": "running"}}
        _set_active_pipeline(p)

        update_task_progress("a", 5, 20, current_id=37)
        assert p._task_states["a"]["current_id"] == 37

        progress_file = tmp_path / "pipeline_progress.json"
        data = json.loads(progress_file.read_text())
        assert data["tasks"]["a"]["current_id"] == 37

        _set_active_pipeline(None)
        db.close()

    def test_current_id_not_set_when_none(self, tmp_path):
        os.environ["DATA_DIR"] = str(tmp_path)
        p = Pipeline()
        p._run_id = "test"
        p._started_at = "2025-01-01T00:00:00Z"
        p._task_states = {"a": {"status": "running"}}
        _set_active_pipeline(p)

        update_task_progress("a", 1, 10)
        assert "current_id" not in p._task_states["a"]

        _set_active_pipeline(None)
