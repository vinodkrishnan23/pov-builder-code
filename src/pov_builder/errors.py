"""Exception hierarchy shared across all nodes/agents.

Deliberately small for Phase 0 — real per-agent error handling (retry
policy, partial-failure semantics) is designed alongside each agent's real
implementation in its own phase, not guessed at here.
"""

from __future__ import annotations


class PovBuilderError(Exception):
    """Base class for every error this project raises intentionally."""


class StateTransitionError(PovBuilderError):
    """A node received state it cannot proceed from (e.g. a required
    upstream field is missing) — distinct from an unexpected/framework
    exception, so routing functions can tell the two apart."""


class ContractValidationError(PovBuilderError):
    """A generated contract (data_model.json, api_contract.json, ...)
    failed structural validation before being committed or consumed."""
