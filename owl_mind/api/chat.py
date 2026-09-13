"""POST /chat -- the one endpoint a student actually calls.

Implements ISSUE-008. Plan reference: section 3.5, section 11.3.

The handler owns no domain logic. It recognises an intent, hands the request to
the orchestrator, and maps the result (or the failure) onto HTTP. Everything
interesting happened in the two issues before this one; this file is where it
becomes reachable.

Two decisions are worth reading the code for.

**Every no-agent outcome is a 200.** There are three of them -- the tutor
handoff, the clarifying question, and the off-topic decline -- and none is an
error. A student whose question fell outside the taxonomy has not made a client
error, and returning 4xx for it would put "user asked about the weather" in the
same bucket as a malformed request.

**No failure body carries internals.** The error map below deliberately loses
information on the way out: the class that raised, the prompt, and the provider
message stay in the logs. What the caller gets is a sentence and a status code.
"""

from __future__ import annotations

import logging
from typing import Any

import anthropic
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from owl_mind.agents.base import AgentRequest, AgentType
from owl_mind.agents.orchestrator import PrimaryAgentFailed
from owl_mind.core.llm_gateway import LLMTimeout, UnknownComponent

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


# An unbounded text field on an unauthenticated endpoint is a token bill with a
# URL. 4000 characters is roughly a long problem statement plus a student's
# attempt, which is the longest thing this is designed to answer.
MAX_MESSAGE_CHARS = 4000


class ChatRequest(BaseModel):
    """One student turn."""

    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    # Accepted, echoed, and otherwise unused. Memory is a later issue; the
    # field exists now so that issue fills it in rather than changing this
    # schema after clients have been written against it. Nothing in this
    # release makes two turns with the same session_id related.
    session_id: str = Field(default="", max_length=128)
    user_id: str = Field(default="", max_length=128)


class ChatResponse(BaseModel):
    """One answer, plus enough of the routing to explain it."""

    request_id: str
    response: str
    # None on the three no-agent paths. See the module docstring: all of them
    # are 200s, and this field is how a client tells them from an answer.
    primary_agent: AgentType | None = None
    supporting_agents: list[AgentType] = Field(default_factory=list)
    # Supporting agents that raised and were dropped. Surfaced so a thinner
    # answer is visibly thinner rather than looking like a narrower route.
    dropped_agents: list[AgentType] = Field(default_factory=list)
    escalated: bool = False
    latency_ms: float = 0.0
    # The primary, the supporting list, and the score vector that produced
    # them. This is the field that makes a surprising route debuggable.
    routing_reason: str = ""
    tools_used: list[str] = Field(default_factory=list)
    # llm_calls, tokens_in, tokens_out, tokens_by_component -- the per-request
    # rollup ISSUE-002 built. Carried on the response, not only in /metrics,
    # so the cost of one answer is attributable without a scrape.
    usage: dict[str, Any] = Field(default_factory=dict)


@router.post("/chat", response_model=ChatResponse)
async def chat(request: Request, body: ChatRequest) -> ChatResponse:
    """Answer one turn.

    Raises:
        HTTPException: 502/503/504 per the error map. Never with a body that
            contains a stack trace, a prompt, or a key.
    """
    recognizer = request.app.state.intent_recognizer
    orchestrator = request.app.state.orchestrator
    gateway = request.app.state.gateway

    # The intent call and the agent calls accumulate into one rollup, so
    # `usage` covers the whole turn rather than just the agent half. The
    # orchestrator opens its own scope for the fan-out; nesting is what makes
    # the intent call land in the same total.
    async with gateway.request_scope() as rollup:
        try:
            intent = await recognizer.recognize(body.message)
            result = await orchestrator.run(
                AgentRequest(
                    message=body.message,
                    intent=intent,
                    session_id=body.session_id,
                    user_id=body.user_id,
                    # Empty until the memory issue. See ChatRequest.session_id.
                    history=[],
                )
            )
        except PrimaryAgentFailed:
            # The chosen specialist did not answer. Deliberately not degraded
            # to another role: a request routed to PracticeAgent and answered
            # by ConceptAgent is a different, weaker product silently
            # substituted for the one asked for.
            logger.exception("primary agent failed")
            raise HTTPException(
                status_code=503,
                detail="The assistant could not answer that right now. Try again shortly.",
            ) from None
        except LLMTimeout:
            logger.warning("request exceeded the model deadline")
            raise HTTPException(
                status_code=504,
                detail="That took too long to answer. Try a shorter question.",
            ) from None
        except anthropic.APIStatusError as exc:
            # Upstream said no. The provider's message can quote the prompt
            # back, so it is logged and not returned.
            logger.warning("provider error: status=%s", exc.status_code)
            raise HTTPException(
                status_code=502,
                detail="The language model is unavailable. Try again shortly.",
            ) from None
        except anthropic.APIConnectionError:
            logger.warning("provider unreachable")
            raise HTTPException(
                status_code=502,
                detail="The language model is unavailable. Try again shortly.",
            ) from None
        except UnknownComponent:
            # A component label that is not in KNOWN_COMPONENTS. That is a bug
            # in this repository, not a bad request, so it is a 500 and it is
            # logged loudly.
            logger.exception("unknown component label reached the gateway")
            raise HTTPException(
                status_code=500,
                detail="Internal error.",
            ) from None

    usage = rollup.as_dict()

    return ChatResponse(
        request_id=result.request_id,
        response=result.response,
        primary_agent=result.primary_agent,
        supporting_agents=result.supporting_agents,
        dropped_agents=result.dropped_agents,
        escalated=result.escalated,
        latency_ms=result.latency_ms,
        routing_reason=result.routing_reason,
        tools_used=result.tools_used,
        usage=usage,
    )
