"""The Agent's HTTP API: the conversation interface the Channel talks to.

    POST /conversations {session_token}            -> {conversation_id}
    POST /conversations/{id}/messages {text}       -> the turn's events, as NDJSON
    POST /conversations/{id}/actions {action, ...} -> the turn's events, as NDJSON

Events are documented in docs/conversation-events.md. The Agent is configured with the
Bank's URL and a model, never the Bank's admin key.
"""

import os
from collections.abc import Iterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from agent.bank import HttpClient
from agent.conversation import (
    Conversations,
    CustomerAction,
    PlanNotPending,
    UnknownConversation,
)
from agent.events import Event
from agent.model import ModelClient, model_from_env


class StartConversation(BaseModel):
    # Only the session token: never the Customer's password or the phone token.
    model_config = ConfigDict(extra="forbid")
    session_token: str = Field(min_length=1)


class CustomerMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)


def ndjson(events: Iterator[Event]) -> Iterator[str]:
    for event in events:
        yield event.model_dump_json() + "\n"


def create_app(bank: HttpClient, model: ModelClient) -> FastAPI:
    """`bank` is an HTTP client whose base URL is the Bank's, holding no credentials."""
    conversations = Conversations(bank, model)
    app = FastAPI(title="Agent")

    @app.exception_handler(UnknownConversation)
    async def on_unknown(_: Request, error: UnknownConversation) -> JSONResponse:
        return JSONResponse(status_code=404, content={"error": "unknown_conversation"})

    @app.exception_handler(PlanNotPending)
    async def on_not_pending(_: Request, error: PlanNotPending) -> JSONResponse:
        return JSONResponse(status_code=409, content={"error": "plan_not_pending"})

    @app.post("/conversations")
    def start_conversation(body: StartConversation) -> dict[str, str]:
        return {"conversation_id": conversations.start(body.session_token).conversation_id}

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

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def main() -> None:
    import uvicorn

    app = create_app(
        bank=httpx.Client(base_url=os.environ.get("BANK_URL", "http://localhost:8000"), timeout=30),
        model=model_from_env(os.environ),
    )
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("AGENT_PORT", "8001")))


if __name__ == "__main__":
    main()
