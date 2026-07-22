# agent-access-control

A small, dependency-free reference implementation of three controls every agentic AI system needs before it touches anything that matters: **access governance**, a **human-in-the-loop action gate**, and an **append-only audit trail**.

This is a sanitized extract from a production AI agent platform I architected for a regulated vertical. It's here as a concrete answer to a question I ask vendors as a third-party risk analyst — *"who can make your AI take an action, how is that gated, and what evidence do you keep?"* — implemented rather than described.

No model, no network, no external services. Pure Python standard library. Every control is covered by tests you can run in seconds.

```bash
pip install pytest
pytest            # 40+ tests, no network, no keys
```

---

## The three controls

### 1. Two-tier access governance — `agent_access/access.py`
**Control domain: least privilege · access governance**

Talking to an agent and making it *act* are different privileges, so they get different gates:

- **Tier 1 — chat allowlist:** who may interact with the agent at all.
- **Tier 2 — action allowlist:** which of those users may make it call a tool. A strict *subset* of Tier 1 — action authority can never exceed chat access.

`None` = unrestricted (single-user default), `[]` = deny everyone, `[ids]` = allow exactly those. Approving another user's pending action is itself an action, so it's gated identically — a chat-only user cannot rubber-stamp a proposal they were never authorized to trigger.

### 2. Human-in-the-loop action gate — `agent_access/action_gate.py`
**Control domain: supervised autonomy · agentic-AI risk**

Every irreversible capability is an `Action` split into `draft()` (a deterministic preview, *no side effect*) and `execute()` (the real effect). Nothing executes until a human explicitly approves. The gate is the single choke point for the whole *propose → resolve → confirm* flow:

- **Deterministic approval, by design.** A reply is classified as approve / cancel / edit / unclear by a fixed vocabulary — **not by an LLM** — so the model can never "creatively" read a reply as consent. This is the core safety property: the thing deciding whether to fire a side effect is deterministic and auditable.
- **Strict mode for unsolicited proposals.** A proactive draft requires an *explicit* "approve" — a bare "yes" that may have been meant for another part of the conversation will not fire it.
- **Edit-intent beats a stray yes/no.** "no, change it to 3pm" is an edit, not a cancel; a stale draft is never sent.
- **Expiry + supersede.** Proposals expire; a newer proposal replaces an older one; every transition is audited.
- **Bounded retry.** Execution retries once, then surfaces the real error — no silent failures.

### 3. Append-only audit log — `agent_access/audit.py`
**Control domain: evidence retention · change management**

One JSON object per line, size-bounded with rotation. The shared trail the action gate (and everything else) writes to: `action_proposed`, `action_executed`, `action_cancelled`, `action_expired`, `action_failed`. This is the evidence a review or an incident investigation actually needs.

---

## Why it exists

Most AI-governance discussion names frameworks; this is the other half — the controls those frameworks describe, built to the standard I assess vendors against. It maps cleanly onto **NIST AI RMF (Manage)**, least-privilege access control, and supervised-autonomy requirements for agentic systems.

Full write-up and related work: [github.com/nefnakhe](https://github.com/nefnakhe)

## Layout

```
agent_access/
  access.py        # two-tier allowlist (chat vs action)
  action_gate.py   # HITL propose/resolve/confirm state machine + deterministic classifier
  audit.py         # append-only, size-bounded JSON audit log
tests/
  test_access.py
  test_action_gate.py
  test_audit.py
```

## License

MIT
