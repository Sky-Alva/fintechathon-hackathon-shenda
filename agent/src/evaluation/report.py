"""The Markdown evaluation report: header, pass counts per task, totals, failures.

`report` takes every run's structured `TaskResult` (N per task when repeated). Further
sections (e.g. the baseline attack table) are passed as `extra_sections`: Markdown blocks
placed after the totals and before the failures.
"""

from collections.abc import Callable, Sequence

from evaluation.runner import TaskResult
from evaluation.scoreboard import CATEGORIES, kind
from evaluation.tasks import InvalidTask


def report(
    results: Sequence[TaskResult],
    *,
    model: str,
    repeat: int,
    date: str,
    commit: str,
    invalid: Sequence[InvalidTask] = (),
    extra_sections: Sequence[str] = (),
) -> str:
    blocks = [
        "\n".join(
            [
                "# Evaluation report",
                "",
                f"- Model: {model}",
                f"- Date: {date}",
                f"- Commit: {commit}",
                f"- Runs per task: {repeat}",
            ]
        ),
        _per_task(results, repeat),
        _totals("By Scenario", "Scenario", results, lambda r: r.task.scenario, sorted),
        _totals(
            "By category",
            "Category",
            results,
            lambda r: r.task.category,
            lambda keys: [c for c in CATEGORIES if c in keys],
        ),
        _totals(
            "By attack type",
            "Attack type",
            [r for r in results if r.task.attack],
            lambda r: r.task.attack or "",
            sorted,
        ),
        *extra_sections,
        _failures(results, repeat),
    ]
    if invalid:
        lines = ["## Not scored", ""] + [f"- {p.path}: {p.problem}" for p in invalid]
        blocks.append("\n".join(lines))
    return "\n\n".join(block for block in blocks if block) + "\n"


def _by_task(results: Sequence[TaskResult]) -> dict[str, list[TaskResult]]:
    runs: dict[str, list[TaskResult]] = {}
    for result in results:
        runs.setdefault(result.task.id, []).append(result)
    return runs


def _per_task(results: Sequence[TaskResult], repeat: int) -> str:
    lines = [
        "## Per task",
        "",
        f"| Task | Kind | Passed (of {repeat}) |",
        "| --- | --- | --- |",
    ]
    for runs in _by_task(results).values():
        passed = sum(run.passed for run in runs)
        lines.append(f"| {runs[0].task.id} | {kind(runs[0])} | {passed}/{len(runs)} |")
    return "\n".join(lines)


def _totals(
    heading: str,
    label: str,
    results: Sequence[TaskResult],
    key: Callable[[TaskResult], str],
    order: Callable[[set[str]], list[str]],
) -> str:
    groups = order({key(r) for r in results})
    if not groups:
        return ""
    lines = [f"## {heading}", "", f"| {label} | Passed runs |", "| --- | --- |"]
    for group in groups:
        members = [r for r in results if key(r) == group]
        lines.append(f"| {group} | {sum(r.passed for r in members)}/{len(members)} |")
    return "\n".join(lines)


def _failures(results: Sequence[TaskResult], repeat: int) -> str:
    lines = ["## Failures", ""]
    failed = False
    for task_id, runs in _by_task(results).items():
        bad = [run for run in runs if not run.passed]
        if not bad:
            continue
        failed = True
        lines.append(f"### {task_id} ({len(bad)} of {len(runs)} runs failed)")
        lines.append("")
        for number, run in enumerate(runs, 1):
            lines += [f"- run {number}: {failure}" for failure in run.failures]
        lines.append("")
    if not failed:
        lines.append("None: every task passed in every run.")
    return "\n".join(lines).rstrip()
