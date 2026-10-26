"""Run every scripted test task (or the named ones) and score them."""

from collections.abc import Callable, Sequence
from fnmatch import fnmatch
from pathlib import Path

from evaluation.runner import HttpClient, TaskResult, run_task
from evaluation.scoreboard import scoreboard
from evaluation.tasks import Task, load_tasks


def evaluate(
    tasks_dir: Path,
    *,
    agent: HttpClient,
    bank: HttpClient,
    admin_key: str,
    model: str,
    only: Sequence[str] = (),
    progress: Callable[[str], None] | None = None,
) -> tuple[list[TaskResult], str]:
    """Run the tasks under `tasks_dir`, one after the other, each from a reset Bank, and
    return their results and the scoreboard naming `model`. `only` keeps the tasks whose
    Scenario or id matches one of its patterns (`cards`, `reference-00*`)."""
    tasks, invalid = load_tasks(tasks_dir)
    results = []
    for task in tasks:
        if only and not any(_named(task, pattern) for pattern in only):
            continue
        result = run_task(task, agent=agent, bank=bank, admin_key=admin_key)
        results.append(result)
        if progress:
            progress(f"{task.id}: {'pass' if result.passed else 'FAIL'}")
    return results, scoreboard(results, model=model, invalid=invalid)


def _named(task: Task, pattern: str) -> bool:
    return fnmatch(task.scenario, pattern) or fnmatch(task.id, pattern)
