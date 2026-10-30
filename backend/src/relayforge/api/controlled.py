"""HTTP application boundary for the stdio adapter; no MCP-specific business logic."""

import secrets

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from relayforge.runtime.bridge_auth import bridge_token
from relayforge.runtime.controlled import ControlledTasks
from relayforge.runtime.evidence import RuntimeEvidence
from relayforge.journal.state import Conflict


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    arguments: dict = Field(default_factory=dict)


class ClientCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_id: str = Field(min_length=1, max_length=200)
    message_id: str = Field(min_length=1, max_length=200)
    tool: str = Field(pattern=r"^relayforge_[a-z_]{1,40}$")
    runtime_call_id: str | None = Field(default=None, pattern=r"^[0-9a-f-]{36}$")
    action_id: str | None = Field(default=None, pattern=r"^[0-9a-f-]{36}$")
    receipt_id: str | None = Field(default=None, max_length=80)
    status: str = Field(max_length=40)


class ClientMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: str = Field(min_length=1, max_length=200)
    text: str = Field(max_length=6000)
    after_call_id: str | None = Field(default=None, max_length=200)


class ClientEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    session_id: str = Field(pattern=r"^ses_[A-Za-z0-9]+$")
    provider: str = Field(pattern=r"^openai$")
    model: str = Field(min_length=1, max_length=100)
    auth_route: str = Field(pattern=r"^chatgpt_oauth$")
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    calls: list[ClientCall] = Field(max_length=100)
    messages: list[ClientMessage] = Field(max_length=100)


def controlled_router(database, settings):
    api = APIRouter(prefix="/api/controlled", tags=["Controlled MCP bridge"])
    credential = bridge_token(settings.mcp_token_path, create=True)
    service = ControlledTasks(database.sessions, settings)

    @api.post("/agent-evidence")
    def agent_evidence(body: ClientEvidence, x_relayforge_mcp: str = Header(default="")):
        if not secrets.compare_digest(x_relayforge_mcp, credential):
            raise HTTPException(403, "Controlled bridge authorization required")
        try:
            return RuntimeEvidence(database.sessions, settings).import_client(body.model_dump())
        except (ValueError, Conflict):
            raise HTTPException(409, "Client evidence binding conflict; existing evidence retained") from None

    @api.post("/{operation}")
    def invoke(operation: str, body: ToolRequest, x_relayforge_mcp: str = Header(default="")):
        if not secrets.compare_digest(x_relayforge_mcp, credential):
            raise HTTPException(403, "Controlled bridge authorization required")
        return service.invoke(operation, body.arguments)

    return api
