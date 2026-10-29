"""`python -m evaluation [scenario-or-task-id ...]`: replay the scripted test tasks with the
configured model and print the scoreboard.

Configuration, from the environment, then the repo's `.env` for anything not set there:

- `AGENT_MODEL_PROVIDER` (`qwen`, the default, or `deepseek`) and that provider's API key,
  exactly as the Agent reads them: switching the model is configuration only.
- `BANK_URL` and `BANK_ADMIN_KEY`: a running Bank (e.g. Docker Compose's, on
  http://localhost:8000), which the runner resets before every task. Without `BANK_URL`,
  the runner starts its own Bank in-process on a temporary database.

`--repeat N` runs each task N times; `--report PATH` writes a Markdown report (header,
pass counts out of N per task, totals, failures). With `--repeat` above 1, the report is
printed instead of the scoreboard.

The Agent runs in-process with the configured model and talks to that Bank as an
untrusted caller, without the admin key. Results aren't deterministic: the runner
measures and compares, it doesn't gate merges, so it exits 0 whatever the score.
"""

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from agent.app import create_app as create_agent
from agent.bank import HttpClient as AgentBankClient
from agent.model import ModelNotConfigured, Provider, model_from_env
from evaluation.report import report
from evaluation.runner import HttpClient, TaskResult
from evaluation.suite import evaluate

REPO = Path(__file__).resolve().parents[3]


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines, as Docker Compose reads `.env`; comments and blanks skipped."""
    if not path.is_file():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("'\"")
    return values


@contextmanager
def bank_from(env: Mapping[str, str]) -> Iterator[tuple[HttpClient, AgentBankClient, str]]:
    """The runner's Bank client, the Agent's (no credentials) and the admin key."""
    url = env.get("BANK_URL")
    if url:
        admin_key = env.get("BANK_ADMIN_KEY", "demo-admin-key")
        with (
            httpx.Client(base_url=url, timeout=120) as runner,
            httpx.Client(base_url=url, timeout=120) as for_agent,
        ):
            yield runner, for_agent, admin_key
        return
    from bank.app import create_app as create_bank  # the dev install has the Bank alongside

    admin_key = secrets.token_urlsafe(16)
    with tempfile.TemporaryDirectory() as directory:
        app = create_bank(db_path=Path(directory) / "bank.sqlite3", admin_key=admin_key)
        with TestClient(app) as client:
            yield client, client, admin_key


def _positive(text: str) -> int:
    number = int(text)
    if number < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return number


def _commit() -> str:
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return done.stdout.strip() or "unknown"


def _markdown(args: argparse.Namespace, results: Sequence[TaskResult], provider: Provider) -> str:
    return report(
        results,
        model=f"{provider.model} ({provider.base_url})",
        repeat=args.repeat,
        date=date.today().isoformat(),
        commit=_commit(),
    )


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evaluation", description=__doc__.split("\n")[0]
    )
    parser.add_argument("only", nargs="*", help="Scenarios or task ids to run (globs); all if none")
    parser.add_argument("--tasks", type=Path, default=REPO / "tasks", help="the tasks directory")
    parser.add_argument("--json", type=Path, help="also write every result to this file")
    parser.add_argument(
        "--repeat", type=_positive, default=1, metavar="N", help="run each task N times"
    )
    parser.add_argument(
        "--report", type=Path, help="also write a Markdown report (pass counts out of N)"
    )
    args = parser.parse_args(argv)
    if env is None:
        env = {**read_env_file(REPO / ".env"), **os.environ}

    try:
        model = model_from_env(env)
    except ModelNotConfigured as problem:
        print(problem, file=sys.stderr)
        return 2
    if not model.api_key:
        print(
            f"Set {model.provider.api_key_env} (in the environment or .env) to run the"
            f" tasks with {model.provider.model}.",
            file=sys.stderr,
        )
        return 2
    with bank_from(env) as (bank, agent_bank, admin_key):
        app = create_agent(bank=agent_bank, model=model, staff_key=secrets.token_urlsafe(16))
        with TestClient(app) as agent:
            results, board = evaluate(
                args.tasks,
                agent=agent,
                bank=bank,
                admin_key=admin_key,
                model=model.provider.model,
                only=args.only,
                repeat=args.repeat,
                progress=lambda line: print(line, file=sys.stderr, flush=True),
            )
    print(board if args.repeat == 1 else _markdown(args, results, model.provider))
    if args.report:
        args.report.write_text(_markdown(args, results, model.provider), encoding="utf-8")
    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "model": model.provider.model,
                    "results": [
                        {
                            "task": result.task.model_dump(),
                            "passed": result.passed,
                            "failures": result.failures,
                            "log": result.log,
                        }
                        for result in results
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
