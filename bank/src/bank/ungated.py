"""The ungated Bank: every Operation is Green at the policy gate. It exists only so the
evaluation can measure what the Bank's enforcement adds (the baseline attack table).

Nothing reaches it from outside: no environment variable, HTTP endpoint, Docker setting or
argument of `bank.app.create_app` builds it. Only code that imports this module, in-process,
can, and only the evaluation runner does.
"""

from pathlib import Path

from fastapi import FastAPI

from bank.app import MODULES, build_app
from bank.registry import Registry


def create_ungated_app(db_path: Path, admin_key: str) -> FastAPI:
    return build_app(db_path, admin_key, Registry(MODULES, ungated=True))
