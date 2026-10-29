"""The Bank's HTTP API.

Customer-facing: log in, read which session a token is, read the Bank clock, read the Operation catalogue, call an
Operation, preview a Plan's tiers, get Confirmation tokens, pass Strong Verification,
read the simulated phone.
Admin (X-Admin-Key): reset, set or advance the Bank clock, read any session's audit
trail, read, set or lift a session's Safety Lock, read any Customer's simulated phone.
"""

import os
import secrets
import sqlite3
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from bank import (
    approvals,
    audit,
    cards,
    clock,
    customers,
    db,
    limits,
    merchants,
    phone,
    policy,
    reserved_funds,
    safety_lock,
    scheduled_transfers,
    sessions,
    spending,
    split_bills,
    transfers,
    verification,
)
from bank.registry import BankModule, Refusal, RefusalReason, Registry

# Order matters: a module's tables may reference tables of the modules before it.
MODULES: list[BankModule] = [
    clock.MODULE,
    customers.MODULE,
    cards.MODULE,
    phone.MODULE,
    transfers.MODULE,
    scheduled_transfers.MODULE,
    split_bills.MODULE,
    reserved_funds.MODULE,
    merchants.MODULE,
    spending.MODULE,
    limits.MODULE,
    sessions.MODULE,
    approvals.MODULE,
    verification.MODULE,
    safety_lock.MODULE,
    audit.MODULE,
]

HTTP_STATUS = {
    RefusalReason.NOT_AUTHENTICATED: 401,
    RefusalReason.NEEDS_CONFIRMATION: 403,
    RefusalReason.NEEDS_VERIFICATION: 403,
    RefusalReason.TOKEN_INVALID: 403,
    RefusalReason.FORBIDDEN_OTHER_CUSTOMER: 403,
    RefusalReason.NOT_FOUND: 404,
    RefusalReason.INSUFFICIENT_FUNDS: 409,
    RefusalReason.AMBIGUOUS_PAYEE: 409,
    RefusalReason.INVALID_INPUT: 422,
    RefusalReason.SESSION_LOCKED: 423,
}


class LoginRequest(BaseModel):
    username: str
    password: str


class SetSafetyLockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    staff: str = Field(min_length=1, max_length=100)
    """The Staff Member setting it, as the staff console names them."""
    reason: str | None = Field(default=None, max_length=1000)


def lock_view(session_id: str, lock: safety_lock.Lock) -> dict[str, Any]:
    """A Customer's Safety Lock as the staff console reads it from one of their sessions."""
    view = {
        "session_id": session_id,
        "locked": True,
        "locked_session_id": lock.session_id,
        "trigger": lock.trigger.value,
        "locked_at": lock.locked_at.isoformat(),
        "locked_until": lock.expires_at.isoformat(),
    }
    if lock.set_by is not None:
        view |= {"set_by": lock.set_by, "reason": lock.reason}
    return view


class SetClockRequest(BaseModel):
    now: datetime


class AdvanceClockRequest(BaseModel):
    minutes: int = Field(gt=0)


def refusal_response(refusal: Refusal) -> JSONResponse:
    return JSONResponse(
        status_code=HTTP_STATUS[refusal.reason],
        content={"refusal": refusal.body()},
    )


def create_app(db_path: Path, admin_key: str) -> FastAPI:
    registry = Registry(MODULES)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if not db.is_initialised(db_path):
            with db.transaction(db_path) as conn:
                db.reset(conn, registry.modules)
        else:
            with db.transaction(db_path) as conn:
                spending.migrate(conn)
        yield

    app = FastAPI(title="Bank", lifespan=lifespan)

    @app.exception_handler(Refusal)
    async def on_refusal(_: Request, refusal: Refusal) -> JSONResponse:
        return refusal_response(refusal)

    @app.exception_handler(RequestValidationError)
    async def on_invalid_request(_: Request, error: RequestValidationError) -> JSONResponse:
        errors = [{k: e[k] for k in ("loc", "msg", "type") if k in e} for e in error.errors()]
        return refusal_response(
            Refusal(RefusalReason.INVALID_INPUT, "Invalid request", {"errors": errors})
        )

    def require_admin(x_admin_key: Annotated[str | None, Header()] = None) -> None:
        if x_admin_key is None or not secrets.compare_digest(x_admin_key, admin_key):
            raise Refusal(RefusalReason.NOT_AUTHENTICATED, "Admin key required")

    # Checked before the body is validated, so a caller without the key learns nothing.
    admin = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

    def bearer_token(authorization: str | None) -> str | None:
        if authorization and authorization.startswith("Bearer "):
            return authorization.removeprefix("Bearer ")
        return None

    def gated[T](
        authorization: str | None,
        step: Callable[[sqlite3.Connection, sessions.Session], T],
        authenticate: Callable[
            [sqlite3.Connection, str | None], sessions.Session
        ] = sessions.authenticate,
    ) -> T:
        """Run one policy-gate step for the calling session (by default identified by its
        session token). A refusal is audited inside the transaction, so commit it first
        and only then refuse."""
        with db.transaction(db_path) as conn:
            session = authenticate(conn, bearer_token(authorization))
            try:
                return step(conn, session)
            except Refusal as refusal:
                pending = refusal
        raise pending

    def whose(session: sessions.Session) -> dict[str, Any]:
        """Which session and Customer this is."""
        return {
            "session_id": session.session_id,
            "customer": {"customer_id": session.customer_id, "name": session.customer_name},
        }

    def run_due_payments(conn: sqlite3.Connection) -> None:
        """Pay what the Bank clock has reached: Scheduled Transfers and Merchant orders."""
        scheduled_transfers.run_due(conn, registry)
        merchants.run_due(conn)

    @app.post("/sessions")
    def log_in(body: LoginRequest) -> dict[str, Any]:
        with db.transaction(db_path) as conn:
            session = sessions.login(conn, body.username, body.password)
        return {
            **whose(session),
            "session_token": session.session_token,
            "phone_token": session.phone_token,
        }

    @app.get("/sessions/current")
    def current_session(authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
        """Which session and Customer a session token is. Not an Operation: not audited."""
        with db.transaction(db_path) as conn:
            session = sessions.authenticate(conn, bearer_token(authorization))
        return whose(session)

    @app.get("/clock")
    def read_clock() -> dict[str, str]:
        with db.transaction(db_path) as conn:
            return {"now": clock.now(conn).isoformat()}

    @app.get("/operations")
    def catalogue() -> dict[str, Any]:
        return {"operations": [op.catalogue_entry() for op in registry.operations.values()]}

    @app.post("/operations/{name}")
    def call_operation(
        name: str,
        authorization: Annotated[str | None, Header()] = None,
        # Parsed as plain JSON; the policy gate validates it so a bad request is audited.
        body: Annotated[Any, Body()] = None,
    ) -> policy.OperationResult:
        return gated(
            authorization,
            lambda conn, session: policy.run(conn, registry, session, name, body),
        )

    @app.post("/confirmations")
    def request_confirmations(
        authorization: Annotated[str | None, Header()] = None,
        body: Annotated[Any, Body()] = None,
    ) -> policy.ConfirmationResult:
        return gated(
            authorization,
            lambda conn, session: policy.issue_confirmations(conn, registry, session, body),
        )

    @app.post("/tier-preview", response_model_exclude_none=True)
    def tier_preview(
        authorization: Annotated[str | None, Header()] = None,
        body: Annotated[Any, Body()] = None,
    ) -> policy.TierPreviewResult:
        return gated(
            authorization,
            lambda conn, session: policy.preview_tiers(conn, registry, session, body),
        )

    @app.post("/verifications")
    def request_verification(
        authorization: Annotated[str | None, Header()] = None,
        body: Annotated[Any, Body()] = None,
    ) -> policy.VerificationChallenge:
        return gated(
            authorization,
            lambda conn, session: policy.start_verification(conn, registry, session, body),
        )

    @app.post("/verifications/{challenge_id}")
    def answer_verification(
        challenge_id: str,
        authorization: Annotated[str | None, Header()] = None,
        body: Annotated[Any, Body()] = None,
    ) -> policy.VerificationResult:
        return gated(
            authorization,
            lambda conn, session: policy.answer_challenge(conn, session, challenge_id, body),
            # Codes come from the Customer's simulated phone, never the Agent's session.
            authenticate=sessions.authenticate_phone,
        )

    @app.get("/phone/messages")
    def read_phone(authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
        with db.transaction(db_path) as conn:
            session = sessions.authenticate_phone(conn, bearer_token(authorization))
            return {"messages": phone.messages(conn, session.customer_id)}

    @admin.post("/reset")
    def reset() -> dict[str, str]:
        with db.transaction(db_path) as conn:
            db.reset(conn, registry.modules)
            return {"now": clock.now(conn).isoformat()}

    @admin.put("/clock")
    def set_clock(body: SetClockRequest) -> dict[str, str]:
        with db.transaction(db_path) as conn:
            now = clock.set_now(conn, body.now)
            run_due_payments(conn)
            return {"now": now.isoformat()}

    @admin.post("/clock/advance")
    def advance_clock(body: AdvanceClockRequest) -> dict[str, str]:
        with db.transaction(db_path) as conn:
            now = clock.advance(conn, timedelta(minutes=body.minutes))
            run_due_payments(conn)
            return {"now": now.isoformat()}

    @admin.get("/sessions/{session_id}/audit")
    def session_audit(session_id: str) -> dict[str, Any]:
        with db.transaction(db_path) as conn:
            if not sessions.exists(conn, session_id):
                raise Refusal(
                    RefusalReason.NOT_FOUND, "No such session", {"session_id": session_id}
                )
            return {"session_id": session_id, "entries": audit.trail(conn, session_id)}

    @admin.get("/sessions/{session_id}/safety-lock")
    def read_safety_lock(session_id: str) -> dict[str, Any]:
        with db.transaction(db_path) as conn:
            if not sessions.exists(conn, session_id):
                raise Refusal(
                    RefusalReason.NOT_FOUND, "No such session", {"session_id": session_id}
                )
            customer_id = sessions.by_id(conn, session_id).customer_id
            lock = safety_lock.active(conn, customer_id, clock.now(conn))
            if lock is None:
                return {"session_id": session_id, "locked": False}
            return lock_view(session_id, lock)

    @admin.post("/sessions/{session_id}/safety-lock")
    def set_safety_lock(session_id: str, body: SetSafetyLockRequest) -> dict[str, Any]:
        with db.transaction(db_path) as conn:
            if not sessions.exists(conn, session_id):
                raise Refusal(
                    RefusalReason.NOT_FOUND, "No such session", {"session_id": session_id}
                )
            lock = policy.set_safety_lock(conn, session_id, body.staff, body.reason)
            return lock_view(session_id, lock)

    @admin.delete("/sessions/{session_id}/safety-lock")
    def lift_safety_lock(session_id: str) -> dict[str, Any]:
        with db.transaction(db_path) as conn:
            if not sessions.exists(conn, session_id):
                raise Refusal(
                    RefusalReason.NOT_FOUND, "No such session", {"session_id": session_id}
                )
            lock = policy.lift_safety_lock(conn, session_id)
            assert lock.lifted_at is not None
            return {
                "session_id": session_id,
                "lock_id": lock.lock_id,
                "locked_session_id": lock.session_id,
                "trigger": lock.trigger.value,
                "lifted_at": lock.lifted_at.isoformat(),
            }

    @admin.get("/customers/{customer_id}/phone/messages")
    def customer_phone(customer_id: str) -> dict[str, Any]:
        with db.transaction(db_path) as conn:
            if not customers.exists(conn, customer_id):
                raise Refusal(
                    RefusalReason.NOT_FOUND, "No such Customer", {"customer_id": customer_id}
                )
            return {"messages": phone.messages(conn, customer_id)}

    app.include_router(admin)
    return app


def main() -> None:
    import uvicorn

    app = create_app(
        db_path=Path(os.environ.get("BANK_DB_PATH", "bank.sqlite3")),
        admin_key=os.environ["BANK_ADMIN_KEY"],
    )
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("BANK_PORT", "8000")))


if __name__ == "__main__":
    main()
