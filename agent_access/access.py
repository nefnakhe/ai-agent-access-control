"""Two-tier access control for an agentic AI system.

Being allowed to *talk* to an agent is not the same as being allowed to make
it *act*. This module separates the two, because an agent that can take
real-world side effects (send mail, create records, move money) needs a
tighter gate on *who can trigger a tool* than on *who can ask a question*.

    Tier 1 — chat allowlist   : who may interact with the agent at all.
    Tier 2 — action allowlist : which of those users may make the agent CALL
                                a tool. A strict subset of Tier 1.

Semantics (deliberate, and covered by tests):

    None  => unrestricted for that tier (backward-compatible with a
             single-user deployment where the sole user is trusted).
    []    => deny everyone for that tier.
    [ids] => allow exactly those ids.

`may_act` requires `may_chat`: the action tier can never grant more reach than
the chat tier. This is least privilege expressed as code — the same control a
third-party risk review looks for when it asks a vendor "who can make your AI
take an action, and how is that separated from who can query it?"
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional


def _norm(ids: Optional[Iterable[str]]) -> Optional[List[str]]:
    return None if ids is None else [str(i).strip() for i in ids if str(i).strip()]


@dataclass
class AccessPolicy:
    chat_allowed: Optional[List[str]] = None      # None => anyone may chat; [] => no one
    action_allowed: Optional[List[str]] = None    # None => any chat-allowed user may act

    @classmethod
    def from_ids(cls, chat_allowed: Optional[Iterable[str]] = None,
                 action_allowed: Optional[Iterable[str]] = None) -> "AccessPolicy":
        return cls(chat_allowed=_norm(chat_allowed), action_allowed=_norm(action_allowed))

    def may_chat(self, user_id: str) -> bool:
        """Tier 1: may this user interact with the agent at all?"""
        return self.chat_allowed is None or user_id in self.chat_allowed

    def may_act(self, user_id: str) -> bool:
        """Tier 2: may this user make the agent call a tool?

        Enforces least privilege: a user who may not chat can never act, and
        the action allowlist only narrows the set further.
        """
        if not self.may_chat(user_id):
            return False
        return self.action_allowed is None or user_id in self.action_allowed

    def may_approve(self, user_id: str) -> bool:
        """Approving another user's pending action IS taking the action, so it
        is gated identically to `may_act`. A chat-only user cannot rubber-stamp
        a proposal they were never authorized to trigger."""
        return self.may_act(user_id)
