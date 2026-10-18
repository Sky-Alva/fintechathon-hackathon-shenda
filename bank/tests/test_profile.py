from typing import Any

import httpx
from fastapi.testclient import TestClient

from conftest import call, login


def refusal(response: httpx.Response) -> Any:
    return response.json()["refusal"]


def test_customer_sees_their_own_and_their_spouses_name_and_birthday(
    client: TestClient,
) -> None:
    response = call(client, login(client, "liming"), "query_profile")

    assert response.status_code == 200, response.text
    assert response.json()["tier"] == "green"
    assert response.json()["output"] == {
        "name": "李明",
        "birthday": "1992-03-15",
        "spouse": {"name": "韩梅梅", "birthday": "1994-10-28"},
    }


def test_each_demo_customer_sees_their_spouse_from_their_own_side(client: TestClient) -> None:
    response = call(client, login(client, "hanmei"), "query_profile")

    assert response.status_code == 200, response.text
    assert response.json()["output"] == {
        "name": "韩梅梅",
        "birthday": "1994-10-28",
        "spouse": {"name": "李明", "birthday": "1992-03-15"},
    }


def test_the_profile_cannot_be_asked_for_another_customer(client: TestClient) -> None:
    response = call(client, login(client, "liming"), "query_profile", {"customer_id": "CUST-HM"})

    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"


def test_the_spouses_balance_is_still_refused_after_reading_the_profile(
    client: TestClient,
) -> None:
    headers = login(client, "liming")
    call(client, headers, "query_profile")

    response = call(client, headers, "query_balance", {"account_id": "ACC-HM-001"})

    assert response.status_code == 403
    assert refusal(response)["reason"] == "forbidden_other_customer"


def test_catalogue_describes_the_profile_query_as_green_and_read_only(client: TestClient) -> None:
    operations = client.get("/operations").json()["operations"]
    entry = next(op for op in operations if op["name"] == "query_profile")

    assert entry["description_zh"] and entry["description_en"]
    assert entry["tier"] == {"kind": "fixed", "tier": "green"}
    assert entry["reversibility"] == {"kind": "read_only"}
    assert entry["input_schema"].get("properties", {}) == {}
