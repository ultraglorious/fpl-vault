import json
import os
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from database_manager import DatabaseManager, backup_db


class Task:
    def __init__(self, name, func, depends_on=None, retries=0, retry_delay=5):
        self.name = name
        self.func = func
        self.depends_on = depends_on or []
        self.retries = retries
        self.retry_delay = retry_delay


class Pipeline:
    def __init__(self, name=""):
        self.name = name
        self._tasks = {}

    def add(self, name, func=None, depends_on=None, retries=0, retry_delay=5):
        if func is None:
            return lambda f: self.add(name, f, depends_on, retries, retry_delay)
        self._tasks[name] = Task(name, func, depends_on, retries, retry_delay)
        return func

    def run(self, db, task_names=None):
        ordered = self._resolve_order(task_names)
        run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
        results = {}

        print(f"\nPipeline run: {run_id}")
        print(f"Tasks: {', '.join(ordered)}")

        for name in ordered:
            task = self._tasks[name]

            dep_failed = self._check_dependencies(results, task)
            if dep_failed:
                print(f"  [SKIP] {name}: dependency '{dep_failed}' failed")
                results[name] = "skipped"
                continue

            log_pipeline_event(run_id, task.name, "started")
            started = time.time()
            try:
                for attempt in range(task.retries + 1):
                    try:
                        task.func(db)
                        break
                    except Exception as e:
                        if attempt < task.retries:
                            print(f"  [RETRY] {task.name} attempt {attempt + 1} failed: {e}")
                            time.sleep(task.retry_delay)
                        else:
                            raise
                duration = round(time.time() - started, 1)
                log_pipeline_event(run_id, task.name, "success", duration_s=duration)
                results[name] = "success"
                print(f"  [OK] {name} ({duration}s)")
            except Exception as e:
                duration = round(time.time() - started, 1)
                log_pipeline_event(run_id, task.name, "failed", duration_s=duration, error=str(e))
                results[name] = "failed"
                print(f"  [FAILED] {name}: {e}")

        self._print_summary(results)
        return results

    def _resolve_order(self, task_names=None):
        names = task_names or list(self._tasks.keys())

        for name in names:
            if name not in self._tasks:
                raise ValueError(f"Task '{name}' not found. Available: {list(self._tasks.keys())}")

        required = set()
        def collect(name):
            if name in required:
                return
            required.add(name)
            for dep in self._tasks[name].depends_on:
                collect(dep)
        for name in names:
            collect(name)

        in_degree = {name: 0 for name in required}
        for name in required:
            for dep in self._tasks[name].depends_on:
                if dep in required:
                    in_degree[name] += 1

        queue = deque([name for name in required if in_degree[name] == 0])
        ordered = []

        while queue:
            name = queue.popleft()
            ordered.append(name)
            for other in required:
                if name in self._tasks[other].depends_on:
                    in_degree[other] -= 1
                    if in_degree[other] == 0:
                        queue.append(other)

        if len(ordered) != len(required):
            raise RuntimeError("Cycle detected in pipeline dependencies")

        return ordered

    def _check_dependencies(self, results, task):
        for dep in task.depends_on:
            if dep not in results:
                return dep
            if results[dep] != "success":
                return dep
        return None

    def _print_summary(self, results):
        success = sum(1 for v in results.values() if v == "success")
        failed = sum(1 for v in results.values() if v == "failed")
        print(f"\nDone. {success} succeeded, {failed} failed.")


def _log_dir():
    return Path(os.getenv("DATA_DIR", "data"))


def log_pipeline_event(run_id, task, status, duration_s=None, error=None):
    entry = {
        "run_id": run_id,
        "task": task,
        "status": status,
        "time": datetime.now(timezone.utc).isoformat(),
    }
    if duration_s is not None:
        entry["duration_s"] = duration_s
    if error is not None:
        entry["error"] = error

    log_dir = _log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(log_dir / "pipeline_runs.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")


def log_discovery(endpoint, table, event, column=None, type=None, was=None, now=None):
    entry = {
        "time": datetime.now(timezone.utc).isoformat(),
        "endpoint": endpoint,
        "table": table,
        "event": event,
    }
    if column is not None:
        entry["column"] = column
    if type is not None:
        entry["type"] = type
    if was is not None:
        entry["was"] = was
    if now is not None:
        entry["now"] = now

    log_dir = _log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(log_dir / "discovery.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")


def build_pipeline():
    from endpoints import (
        ingest_bootstrap,
        ingest_dream_team,
        ingest_element_summary,
        ingest_event_live,
        ingest_simple,
    )

    p = Pipeline(name="fpl_ingest")

    p.add("bootstrap-static", func=ingest_bootstrap, depends_on=[])

    p.add("event-status", func=lambda db: ingest_simple("event-status/", db), depends_on=[])
    p.add("fixtures", func=lambda db: ingest_simple("fixtures/", db), depends_on=[])
    p.add("set-piece-notes", func=lambda db: ingest_simple("team/set-piece-notes/", db, table_prefix="team_set_piece_notes"), depends_on=[])

    p.add("event-live", func=ingest_event_live, depends_on=["bootstrap-static"])
    p.add("dream-team", func=ingest_dream_team, depends_on=["bootstrap-static"])
    p.add("element-summary", func=ingest_element_summary, depends_on=["bootstrap-static"])

    return p


def build_init_pipeline():
    from endpoints import (
        init_bootstrap,
        init_dream_team,
        init_element_summary,
        init_event_live,
        init_simple,
    )

    p = Pipeline(name="fpl_init")

    p.add("bootstrap-static", func=init_bootstrap, depends_on=[])

    p.add("event-status", func=lambda db: init_simple("event-status/", db), depends_on=[])
    p.add("fixtures", func=lambda db: init_simple("fixtures/", db), depends_on=[])
    p.add("set-piece-notes", func=lambda db: init_simple("team/set-piece-notes/", db, table_prefix="team_set_piece_notes"), depends_on=[])

    p.add("event-live", func=init_event_live, depends_on=[])
    p.add("dream-team", func=init_dream_team, depends_on=[])
    p.add("element-summary", func=init_element_summary, depends_on=[])

    return p


def main():
    load_dotenv()

    init_mode = "--init" in sys.argv
    live = "--live" in sys.argv
    task_filter = None

    for i, arg in enumerate(sys.argv):
        if arg == "--tasks" and i + 1 < len(sys.argv):
            task_filter = sys.argv[i + 1].split(",")

    db_name = os.getenv("DB_NAME", "footballdb")
    data_dir = os.getenv("DATA_DIR", "data")
    suffix = "" if live else "_dev"
    db_path = f"{data_dir}/{db_name}{suffix}.duckdb"

    if live and not init_mode:
        backup_db(db_path)

    db = DatabaseManager(db_path, schema="fpl_api")
    print(f"Target database: {db_path}")
    print(f"Mode: {'init (schema only)' if init_mode else 'ingest'}")

    pipeline = build_init_pipeline() if init_mode else build_pipeline()

    try:
        pipeline.run(db, task_names=task_filter)
    finally:
        db.close()


if __name__ == "__main__":
    main()
