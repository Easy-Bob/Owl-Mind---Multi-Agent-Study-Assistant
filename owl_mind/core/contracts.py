"""Startup contracts -- invariants that must fail at boot, not at 3am.

Implements ISSUE-001 FR4 (the hook). The checks themselves arrive with the
issues that create the things being checked.

Plan reference: section 11.3, highlight 11 ("contracts as assertions"). The
reference implementation kept these invariants in prose, so taxonomy drift and
tool-scope disagreement surfaced as confusing runtime behaviour rather than as
a refusal to start.

To add a check::

    @contract("every IntentCategory has few-shot templates")
    def _check_taxonomy_sync() -> None:
        missing = set(IntentCategory) - set(_TEMPLATES)
        if missing:
            raise ContractViolation(f"intents without templates: {missing}")

Checks run at import-time registration order, and all failures are reported
together -- fixing one config error only to be shown the next one is a poor
way to spend a morning.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

ContractCheck = Callable[[], None]

# (description, check) pairs, populated by the @contract decorator.
_REGISTRY: list[tuple[str, ContractCheck]] = []


class ContractViolation(AssertionError):
    """A startup invariant does not hold."""


def contract(description: str) -> Callable[[ContractCheck], ContractCheck]:
    """Register a startup check. The description appears in failure output."""

    def register(check: ContractCheck) -> ContractCheck:
        _REGISTRY.append((description, check))
        return check

    return register


def registered_contracts() -> list[str]:
    """Descriptions of every registered check, for /health and for tests."""
    return [description for description, _ in _REGISTRY]


def verify_startup_contracts() -> None:
    """Run every registered check.

    Raises:
        ContractViolation: listing *all* failures, not just the first.
    """
    failures: list[str] = []

    for description, check in _REGISTRY:
        try:
            check()
        except ContractViolation as exc:
            failures.append(f"  - {description}: {exc}")
        except Exception as exc:  # a broken check is itself a contract failure
            failures.append(f"  - {description}: check raised {type(exc).__name__}: {exc}")

    if failures:
        raise ContractViolation(
            f"{len(failures)} startup contract(s) violated:\n" + "\n".join(failures)
        )

    logger.info("startup contracts verified (%d registered)", len(_REGISTRY))


# ---------------------------------------------------------------------------
# Checks to be registered by later issues:
#
#   - every IntentCategory appears in _TEMPLATES and _INTENT_GROUPS
#     (plan 3.2; prevents silent taxonomy drift)
#   - set(profile.tool_scope) == set(agent.get_tools()) for every agent
#     (plan 4, Day 4; prevents a whitelist that does not match reality)
#   - every registered tool name is unique across agents
# ---------------------------------------------------------------------------
