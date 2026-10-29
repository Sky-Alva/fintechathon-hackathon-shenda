"""The evaluation runner: replays the scripted test tasks under tasks/ against the Agent
and the Bank and prints a scoreboard. Run it with `python -m evaluation` (see __main__.py).
"""

from evaluation.runner import TaskResult, matches, run_task
from evaluation.report import report
from evaluation.scoreboard import scoreboard
from evaluation.suite import evaluate
from evaluation.tasks import InvalidTask, Task, load_task, load_tasks

__all__ = [
    "InvalidTask",
    "Task",
    "TaskResult",
    "evaluate",
    "load_task",
    "load_tasks",
    "matches",
    "report",
    "run_task",
    "scoreboard",
]
