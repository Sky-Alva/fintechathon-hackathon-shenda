"""The scoreboard: pass/fail per task, with totals per Scenario, category and attack type."""

from collections.abc import Callable, Sequence

from evaluation.runner import TaskResult
from evaluation.tasks import InvalidTask

CATEGORIES = ("normal", "clarification", "attack")


def scoreboard(
    results: Sequence[TaskResult], *, model: str, invalid: Sequence[InvalidTask] = ()
) -> str:
    passed = sum(result.passed for result in results)
    lines = [f"Scoreboard: {model}, {passed}/{len(results)} tasks passed", ""]
    id_width = max((len(r.task.id) for r in results), default=0)
    kind_width = max((len(kind(r)) for r in results), default=0)
    for result in results:
        mark = "pass" if result.passed else "FAIL"
        lines.append(
            f"  {mark}  {result.task.id:<{id_width}}  {kind(result):<{kind_width}}"
            f"  {result.task.title}"
        )
        lines += [f"          {failure}" for failure in result.failures]
    lines += _totals("By Scenario", results, lambda r: r.task.scenario, sorted)
    lines += _totals(
        "By category",
        results,
        lambda r: r.task.category,
        lambda keys: [c for c in CATEGORIES if c in keys],
    )
    attacks = [r for r in results if r.task.attack]
    lines += _totals("By attack type", attacks, lambda r: r.task.attack or "", sorted)
    if invalid:
        files = "file" if len(invalid) == 1 else "files"
        lines += ["", f"Not scored: {len(invalid)} invalid task {files}"]
        lines += [f"  {problem.path}: {problem.problem}" for problem in invalid]
    return "\n".join(lines)


def kind(result: TaskResult) -> str:
    task = result.task
    return f"{task.category}/{task.attack}" if task.attack else task.category


def _totals(
    heading: str,
    results: Sequence[TaskResult],
    key: Callable[[TaskResult], str],
    order: Callable[[set[str]], list[str]],
) -> list[str]:
    groups = order({key(r) for r in results})
    if not groups:
        return []
    width = max(len(group) for group in groups)
    lines = ["", heading]
    for group in groups:
        members = [r for r in results if key(r) == group]
        passed = sum(r.passed for r in members)
        lines.append(f"  {group:<{width}}   {passed}/{len(members)}")
    return lines
