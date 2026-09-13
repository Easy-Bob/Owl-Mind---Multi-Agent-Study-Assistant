"""ResponseComposer -- merges several agent answers into one reply.

Implements ISSUE-005 FR5.

Only runs when more than one agent answered. A single-agent result is returned
verbatim: paying for a model call to reformat one answer is waste, and it is
the most common shape by a wide margin.

The merge instruction is "keep both answers", not "summarise them". The student
asked two things; averaging two specialists into one paragraph is how a
multi-agent system produces worse output than either agent alone.
"""

from __future__ import annotations

from owl_mind.agents.base import AgentResponse
from owl_mind.core.llm_gateway import LLMGateway

COMPOSER_MAX_TOKENS = 1400

_SYSTEM = (
    "You merge answers from several specialist study assistants into one reply "
    "to a student.\n\n"
    "Rules:\n"
    "- Keep every specialist's contribution. The student asked for more than "
    "one thing and should receive more than one answer.\n"
    "- Do not summarise the answers into a single paragraph, and do not drop "
    "the shorter one.\n"
    "- Order the parts the way the student asked for them.\n"
    "- Add nothing of your own: no new facts, no extra examples, no closing "
    "offer of further help.\n"
    "- Write the seams, not the content. One short connecting sentence between "
    "parts is enough."
)


class ResponseComposer:
    """Joins multiple agent responses through the gateway."""

    def __init__(self, gateway: LLMGateway) -> None:
        self._gateway = gateway

    async def compose(self, message: str, responses: list[AgentResponse]) -> str:
        """Merge two or more answers. One answer is returned unchanged.

        Raises:
            ValueError: if called with nothing to merge. An empty list means
                every agent failed, which is the orchestrator's decision to
                make, not a reply to compose.
        """
        if not responses:
            raise ValueError("nothing to compose")
        if len(responses) == 1:
            return responses[0].content

        parts = "\n\n".join(
            f"[{response.agent_type.value}]\n{response.content}" for response in responses
        )
        prompt = (
            f"The student asked:\n{message}\n\n"
            f"The specialists answered:\n\n{parts}\n\n"
            "Merge these into one reply to the student."
        )
        completion = await self._gateway.complete(
            component="composer",
            max_tokens=COMPOSER_MAX_TOKENS,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        for block in getattr(completion, "content", None) or []:
            if getattr(block, "type", None) == "text":
                return str(getattr(block, "text", ""))

        # A composer that returns nothing usable must not lose the answers it
        # was given. Falling back to the labelled parts is worse prose and
        # strictly better than an empty reply.
        return parts
