"""The audit log is a hash chain: it cannot be altered without anyone noticing."""

import sqlite3
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from conftest import admin, call, login_session

HUSBAND = "PAY-HM-001"


def verify(client: TestClient) -> dict[str, Any]:
    result: dict[str, Any] = admin(client, "get", "/admin/audit/verify")
    return result


def some_activity(client: TestClient) -> str:
    session_id, headers = login_session(client, "liming")
    for _ in range(3):
        assert call(client, headers, "read_audit_trail").is_success
    call(client, headers, "transfer", {"payee_id": HUSBAND, "amount": "5.00"})
    _, other = login_session(client, "hanmei")
    assert call(client, other, "read_audit_trail").is_success
    return session_id


def entry_ids(db_path: Path) -> list[int]:
    conn = sqlite3.connect(db_path)
    try:
        return [r[0] for r in conn.execute("SELECT entry_id FROM audit_log ORDER BY entry_id")]
    finally:
        conn.close()


def tamper(db_path: Path, sql: str, *args: Any) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()


def test_the_chain_verifies_after_a_normal_session(client: TestClient) -> None:
    some_activity(client)
    result = verify(client)
    assert result["ok"] is True
    assert result["entries_checked"] > 2
    assert result["first_broken_entry_id"] is None


def test_an_empty_log_verifies(client: TestClient) -> None:
    assert verify(client) == {"ok": True, "entries_checked": 0, "first_broken_entry_id": None}


def test_editing_an_entrys_input_breaks_the_chain_at_that_entry(
    client: TestClient, db_path: Path
) -> None:
    some_activity(client)
    target = entry_ids(db_path)[2]
    tamper(db_path, "UPDATE audit_log SET input = ? WHERE entry_id = ?", '{"x": 1}', target)
    result = verify(client)
    assert result["ok"] is False
    assert result["first_broken_entry_id"] == target


def test_deleting_an_entry_breaks_the_chain_at_the_next_entry(
    client: TestClient, db_path: Path
) -> None:
    some_activity(client)
    ids = entry_ids(db_path)
    tamper(db_path, "DELETE FROM audit_log WHERE entry_id = ?", ids[2])
    result = verify(client)
    assert result["ok"] is False
    assert result["first_broken_entry_id"] == ids[3]


def test_inserting_an_entry_breaks_the_chain_at_that_entry(
    client: TestClient, db_path: Path
) -> None:
    some_activity(client)
    ids = entry_ids(db_path)
    new_id = ids[2] + 1000
    tamper(
        db_path,
        "INSERT INTO audit_log (entry_id, session_id, at, event, operation, input, tier,"
        " outcome, reason, detail, caller_detail, prev_hash, hash)"
        " VALUES (?, 's', '2026-10-20T09:00:00+08:00', 'operation', 'x', '{}', NULL, 'ok',"
        " NULL, '{}', NULL, 'a', 'b')",
        new_id,
    )
    result = verify(client)
    assert result["ok"] is False
    assert result["first_broken_entry_id"] == new_id


def test_the_chain_is_valid_again_after_a_reset(client: TestClient, db_path: Path) -> None:
    some_activity(client)
    tamper(db_path, "UPDATE audit_log SET input = '[]' WHERE entry_id = ?", entry_ids(db_path)[0])
    assert verify(client)["ok"] is False
    admin(client, "post", "/admin/reset")
    assert verify(client)["ok"] is True
    some_activity(client)
    assert verify(client)["ok"] is True


def test_entries_in_every_view_carry_a_hash(client: TestClient) -> None:
    session_id, headers = login_session(client, "liming")
    assert call(client, headers, "read_audit_trail").is_success
    own = call(client, headers, "read_audit_trail").json()["output"]["entries"]
    staff = admin(client, "get", f"/admin/sessions/{session_id}/audit")["entries"]
    assert own and staff
    assert all(len(e["hash"]) == 64 for e in own + staff)
    assert verify(client)["ok"] is True


def test_verifying_needs_the_admin_key(client: TestClient) -> None:
    assert client.get("/admin/audit/verify").status_code in (401, 403)
    wrong = client.get("/admin/audit/verify", headers={"X-Admin-Key": "nope"})
    assert wrong.status_code in (401, 403)
