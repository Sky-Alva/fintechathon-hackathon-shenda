"""The ungated Bank exists only for the evaluation baseline: it tiers everything Green. It can't
be asked for through the normal constructor, the environment or HTTP."""

import inspect
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bank.app import create_app
from bank.ungated import create_ungated_app
from conftest import ADMIN_KEY, call, login

TO_MUM = {"payee_id": "PAY-LM-002", "amount": "300.00", "note": ""}


def test_the_normal_bank_still_asks_for_a_confirmation(client: TestClient) -> None:
    headers = login(client, "liming")
    assert call(client, headers, "transfer", TO_MUM).status_code != 200


def test_the_ungated_bank_runs_a_transfer_with_no_approval(tmp_path: Path) -> None:
    app = create_ungated_app(db_path=tmp_path / "u.sqlite3", admin_key=ADMIN_KEY)
    with TestClient(app) as client:
        headers = login(client, "liming")
        response = call(client, headers, "transfer", TO_MUM)
        assert response.status_code == 200, response.text
        assert response.json()["tier"] == "green"


def test_the_normal_constructor_has_no_way_to_switch_the_gate_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert list(inspect.signature(create_app).parameters) == ["db_path", "admin_key"]
    for name in ("BANK_UNGATED", "BANK_GATE_OFF", "UNGATED"):
        monkeypatch.setenv(name, "1")
    with TestClient(create_app(tmp_path / "n.sqlite3", ADMIN_KEY)) as client:
        headers = login(client, "liming")
        assert call(client, headers, "transfer", TO_MUM).status_code != 200
