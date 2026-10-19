"""The Agent's HTTP API: the conversation interface the Channel talks to.

    POST /conversations {session_token}            -> {conversation_id}
    POST /conversations/{id}/messages {text}       -> the turn's events, as NDJSON
    POST /conversations/{id}/actions {action, ...} -> the turn's events, as NDJSON
    GET  /conversations/{id}/audit-trail           -> a `trace` event: the session's audit trail

For Staff Members, with the Agent's own staff key (X-Staff-Key):

    GET /staff/conversations                       -> the active sessions' conversations
    GET /staff/conversations/{id}                  -> one conversation's history

Events are documented in docs/conversation-events.md. The Agent is configured with the
Bank's URL, a model and its staff key, never the Bank's admin key: the staff console
reads Bank audit trails and lifts Safety Locks with the Bank directly.
"""

import os
import secrets
from collections.abc import Iterator
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from agent.bank import BankRefused, BankUnavailable, HttpClient
from agent.conversation import (
    Conversation,
    Conversations,
    CustomerAction,
    PlanNotPending,
    PlanNotRunning,
    UnknownConversation,
    UnknownStep,
)
from agent.events import Event, Trace
from agent.model import ModelClient, model_from_env


class StartConversation(BaseModel):
    # Only the session token: never the Customer's password or the phone token.
    model_config = ConfigDict(extra="forbid")
    session_token: str = Field(min_length=1)


class CustomerMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)


class StaffKeyRequired(Exception):
    pass


def ndjson(events: Iterator[Event]) -> Iterator[str]:
    for event in events:
        yield event.model_dump_json() + "\n"


def staff_summary(conversation: Conversation) -> dict[str, Any]:
    return {
        "conversation_id": conversation.conversation_id,
        "session_id": conversation.session_id,
        "customer": conversation.customer,
    }


def create_app(bank: HttpClient, model: ModelClient, staff_key: str) -> FastAPI:
    """`bank` is an HTTP client whose base URL is the Bank's, holding no credentials.
    `staff_key` guards the staff views: it is the Agent's own, not the Bank's admin key."""
    conversations = Conversations(bank, model)
    app = FastAPI(title="Agent")

    @app.exception_handler(UnknownConversation)
    async def on_unknown(_: Request, error: UnknownConversation) -> JSONResponse:
        return JSONResponse(status_code=404, content={"error": "unknown_conversation"})

    @app.exception_handler(PlanNotPending)
    async def on_not_pending(_: Request, error: PlanNotPending) -> JSONResponse:
        return JSONResponse(status_code=409, content={"error": "plan_not_pending"})

    @app.exception_handler(UnknownStep)
    async def on_unknown_step(_: Request, error: UnknownStep) -> JSONResponse:
        return JSONResponse(status_code=422, content={"error": "unknown_step"})

    @app.exception_handler(PlanNotRunning)
    async def on_not_running(_: Request, error: PlanNotRunning) -> JSONResponse:
        return JSONResponse(status_code=409, content={"error": "plan_not_running"})

    @app.exception_handler(BankUnavailable)
    async def on_bank_unavailable(_: Request, error: BankUnavailable) -> JSONResponse:
        # Outside a turn (starting a conversation, the staff views): say so, guess nothing.
        return JSONResponse(status_code=503, content={"error": "bank_unavailable"})

    @app.exception_handler(StaffKeyRequired)
    async def on_staff_key_required(_: Request, error: StaffKeyRequired) -> JSONResponse:
        return JSONResponse(status_code=401, content={"error": "staff_key_required"})

    def require_staff(x_staff_key: Annotated[str | None, Header()] = None) -> None:
        if x_staff_key is None or not secrets.compare_digest(x_staff_key, staff_key):
            raise StaffKeyRequired()

    # Checked before anything else, so a caller without the key learns nothing.
    staff = APIRouter(prefix="/staff", dependencies=[Depends(require_staff)])

    @app.post("/conversations")
    def start_conversation(body: StartConversation) -> Any:
        try:
            conversation = conversations.start(body.session_token)
        except BankRefused as refused:
            if refused.reason != "not_authenticated":
                raise
            return JSONResponse(status_code=401, content={"error": "not_authenticated"})
        return {"conversation_id": conversation.conversation_id}

    @app.post("/conversations/{conversation_id}/messages")
    def send_message(conversation_id: str, body: CustomerMessage) -> StreamingResponse:
        conversation = conversations.get(conversation_id)
        return StreamingResponse(
            ndjson(conversations.send(conversation, body.text)),
            media_type="application/x-ndjson",
            headers={"X-Accel-Buffering": "no"},
        )

    @app.post("/conversations/{conversation_id}/actions")
    def send_action(conversation_id: str, body: CustomerAction) -> StreamingResponse:
        conversation = conversations.get(conversation_id)
        return StreamingResponse(
            ndjson(conversations.act(conversation, body)),
            media_type="application/x-ndjson",
            headers={"X-Accel-Buffering": "no"},
        )

    @app.get("/conversations/{conversation_id}/audit-trail")
    def audit_trail(conversation_id: str) -> Trace:
        return conversations.audit_trail(conversations.get(conversation_id))

    @staff.get("/conversations")
    def active_conversations() -> dict[str, Any]:
        return {"conversations": [staff_summary(c) for c in conversations.active()]}

    @staff.get("/conversations/{conversation_id}")
    def conversation_history(conversation_id: str) -> dict[str, Any]:
        """Readable after the session ended too, for whoever investigates it."""
        conversation = conversations.get(conversation_id)
        return {
            **staff_summary(conversation),
            "entries": [entry.model_dump(mode="json") for entry in list(conversation.record)],
        }

    app.include_router(staff)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def main() -> None:
    import uvicorn

    app = create_app(
        bank=httpx.Client(base_url=os.environ.get("BANK_URL", "http://localhost:8000"), timeout=30),
        model=model_from_env(os.environ),
        staff_key=os.environ["AGENT_STAFF_KEY"],
    )
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("AGENT_PORT", "8001")))


if __name__ == "__main__":
    main()
