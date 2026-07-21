"""Human-in-the-loop action gate. Any outbound/irreversible capability is
an Action split into draft() (a deterministic preview, no side effect) and execute()
(the real effect). Nothing executes until the human explicitly approves.

The gate is the single choke point for the whole propose -> resolve -> confirm flow:
- propose() shows the coach a preview and registers ONE pending proposal per conversation
  (a newer proposal supersedes the older).
- resolve() reads the coach's reply with a DETERMINISTIC affirmative/negative/edit vocabulary
  (no LLM, so it can never "creatively" read a reply as approval) and, only on a clear
  approval of a live proposal, executes with a retry-once policy.
- Proposals expire after a window. Every transition is audited.

External tool-backed actions register here; the state machine ships with stub Actions.
"""
from __future__ import annotations

import itertools
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional


@dataclass
class Action:
    name: str
    draft: Callable[..., str]     # deterministic preview; no side effects
    execute: Callable[..., Any]   # performs the effect


@dataclass
class Resolution:
    """Outcome of resolving a coach reply against a pending proposal."""
    kind: str  # none | expired | done | failed | cancelled | edit | unclear
    result: Any = None
    error: str = ""
    reprocess_text: str = ""  # for edit / give-up: the loop re-runs normal answering on this


@dataclass
class _Pending:
    pid: str
    action: Action
    args: dict
    created_at: float
    asked_again: bool = False
    strict: bool = False   # proactive/unsolicited proposal: needs an explicit APPROVE, not a bare yes


# An explicit cancel word ends the proposal outright; a soft "no" only cancels when there's no
# edit intent (so "no, change it to 3pm" is an EDIT, not a cancel — the reported bug). Edit intent
# beats both a soft "no" and a "yes" (wanting a change means keep-and-modify, never send stale).
# Soft negatives are still checked BEFORE approval, so "don't send it" can't match the "send it"
# approve-phrase and fire by accident.
_HARD_CANCEL = {"cancel", "cancelled", "stop", "scrap", "discard", "nevermind"}
_HARD_CANCEL_PHRASES = ("never mind", "forget it", "call it off")
_EDIT_WORDS = {"change", "edit", "update", "reword", "revise", "rewrite", "redo", "tweak",
               "adjust", "modify", "rephrase", "replace", "fix", "shorten", "shorter",
               "longer", "instead", "actually", "different", "reschedule"}
_EDIT_PHRASES = ("make it", "move it", "change it")
_SOFT_NEG = {"no", "nope", "nah", "dont", "don't", "not", "negative"}
# Explicit approvals are deliberate send commands; casual ones are throwaway affirmatives. A
# `strict` proposal (an unsolicited/proactive draft) requires an EXPLICIT approval, so a stray
# "yes" meant for another part of the conversation can't fire it. Reactive proposals accept both.
_EXPLICIT_APPROVE = {"approve", "approved", "confirm", "confirmed"}
_EXPLICIT_APPROVE_PHRASES = ("go ahead", "do it", "send it", "go for it")
_CASUAL_APPROVE = {"yes", "yeah", "yep", "yup", "yea", "sure", "ok", "okay"}
_CASUAL_APPROVE_PHRASES = ("sounds good", "looks good", "yes please")


def _classify(reply: str, strict: bool = False) -> str:
    low = reply.strip().lower()
    words = set(re.findall(r"[a-z']+", low))
    if words & _HARD_CANCEL or any(p in low for p in _HARD_CANCEL_PHRASES):
        return "cancel"
    if words & _EDIT_WORDS or any(p in low for p in _EDIT_PHRASES):   # edit beats a soft no / a yes
        return "edit"
    if words & _SOFT_NEG:                                             # a plain "no" cancels
        return "cancel"
    if words & _EXPLICIT_APPROVE or any(p in low for p in _EXPLICIT_APPROVE_PHRASES):
        return "approve"
    if words & _CASUAL_APPROVE or any(p in low for p in _CASUAL_APPROVE_PHRASES):
        return "unclear" if strict else "approve"                    # strict: bare "yes" isn't enough
    return "unclear"


class ActionGate:
    def __init__(self, audit=None, clock: Optional[Callable[[], float]] = None,
                 expiry_seconds: float = 900):
        self.audit = audit
        self.clock = clock or time.time
        self.expiry = expiry_seconds
        self._pending: Dict[str, _Pending] = {}   # conversation_id -> pending
        self._seq = itertools.count(1)

    def propose(self, conversation_id: str, action: Action, strict: bool = False, **args) -> tuple:
        preview = action.draft(**args)             # deterministic, no side effect
        pid = str(next(self._seq))
        self._pending[conversation_id] = _Pending(pid, action, args, self.clock(), strict=strict)
        self._audit("action_proposed", id=pid, action=action.name, conversation_id=conversation_id)
        return pid, preview

    def has_pending(self, conversation_id: str) -> bool:
        return conversation_id in self._pending

    def has_live_pending(self, conversation_id: str) -> bool:
        """Expiry-aware has_pending: an expired proposal is pruned (and audited) and reported as
        not-live, so a stale/abandoned proposal never blocks a new one (e.g. the proactive email
        watcher would otherwise freeze forever behind one un-answered draft). The loop keeps using
        plain has_pending, so a late reply still gets the 'that proposal expired' notice via
        resolve()."""
        p = self._pending.get(conversation_id)
        if p is None:
            return False
        if self.clock() - p.created_at > self.expiry:
            del self._pending[conversation_id]
            self._audit("action_expired", id=p.pid, action=p.action.name,
                        conversation_id=conversation_id)
            return False
        return True

    def resolve(self, conversation_id: str, reply: str) -> Resolution:
        p = self._pending.get(conversation_id)
        if p is None:
            return Resolution("none")
        if self.clock() - p.created_at > self.expiry:
            del self._pending[conversation_id]
            self._audit("action_expired", id=p.pid, action=p.action.name,
                        conversation_id=conversation_id)
            return Resolution("expired")
        intent = _classify(reply, strict=p.strict)
        if intent == "approve":
            del self._pending[conversation_id]
            return self._execute(conversation_id, p)
        if intent == "cancel":
            del self._pending[conversation_id]
            self._audit("action_cancelled", id=p.pid, action=p.action.name,
                        conversation_id=conversation_id)
            return Resolution("cancelled")
        if intent == "edit":
            del self._pending[conversation_id]
            return Resolution("edit", reprocess_text=reply)
        # unclear: re-ask once, then give up and let the loop treat it as normal chat
        if not p.asked_again:
            p.asked_again = True
            return Resolution("unclear")
        del self._pending[conversation_id]
        return Resolution("none", reprocess_text=reply)

    def _execute(self, conversation_id: str, p: _Pending) -> Resolution:
        try:
            result = self._run_with_retry(p.action, p.args)
        except Exception as e:  # retried once already; surface the real error
            self._audit("action_failed", id=p.pid, action=p.action.name, error=str(e),
                        conversation_id=conversation_id)
            return Resolution("failed", error=str(e))
        self._audit("action_executed", id=p.pid, action=p.action.name,
                    conversation_id=conversation_id)
        return Resolution("done", result=result)

    @staticmethod
    def _run_with_retry(action: Action, args: dict) -> Any:
        try:
            return action.execute(**args)
        except Exception:
            return action.execute(**args)  # one retry; a second failure propagates

    def _audit(self, event: str, **details) -> None:
        if self.audit:
            self.audit.append(event, **details)
