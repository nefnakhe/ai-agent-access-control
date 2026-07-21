"""HITL action-gate state machine : propose (deterministic preview, no side
effect) -> resolve a reply (approve/cancel/edit/unclear) with a deterministic vocabulary
-> execute only on approval (retry once), all per-conversation with expiry + supersede.
Proven with stub Actions — no model, no external backend, no network."""
from agent_access.action_gate import Action, ActionGate, Resolution, _classify
from agent_access.audit import AuditLog


def _recording_action(name="create_event", fail_times=0):
    calls = {"executed": 0}

    def draft(**kw):
        return "DRAFT: " + ", ".join(f"{k}={v}" for k, v in sorted(kw.items()))

    def execute(**kw):
        calls["executed"] += 1
        if calls["executed"] <= fail_times:
            raise RuntimeError("backend down")
        return {"ok": True, **kw}

    return Action(name=name, draft=draft, execute=execute), calls


def test_propose_previews_without_side_effect():
    action, calls = _recording_action()
    gate = ActionGate()
    pid, preview = gate.propose("c1", action, title="Call", start="3pm")
    assert "DRAFT:" in preview and "title=Call" in preview
    assert calls["executed"] == 0


def test_affirmative_reply_executes_once():
    action, calls = _recording_action()
    gate = ActionGate()
    gate.propose("c1", action, title="Call")
    r = gate.resolve("c1", "yes, go ahead")
    assert r.kind == "done" and r.result["ok"] is True
    assert calls["executed"] == 1


def test_unrelated_reply_does_not_execute():
    action, calls = _recording_action()
    gate = ActionGate()
    gate.propose("c1", action, title="Call")
    r = gate.resolve("c1", "what's my next session?")
    assert r.kind == "unclear"
    assert calls["executed"] == 0


def test_negative_reply_cancels():
    action, calls = _recording_action()
    gate = ActionGate()
    gate.propose("c1", action, title="Call")
    r = gate.resolve("c1", "no, cancel that")
    assert r.kind == "cancelled"
    assert calls["executed"] == 0
    assert gate.resolve("c1", "yes").kind == "none"  # nothing pending anymore


def test_resolve_with_no_pending_is_none():
    gate = ActionGate()
    assert gate.resolve("c1", "yes").kind == "none"


def test_newer_proposal_supersedes_older():
    action, calls = _recording_action()
    gate = ActionGate()
    gate.propose("c1", action, title="First")
    gate.propose("c1", action, title="Second")
    r = gate.resolve("c1", "approve")
    assert r.kind == "done" and r.result["title"] == "Second"


def test_expired_proposal_does_not_execute():
    action, calls = _recording_action()
    now = {"t": 1000.0}
    gate = ActionGate(clock=lambda: now["t"], expiry_seconds=900)
    gate.propose("c1", action, title="Call")
    now["t"] = 1000.0 + 901
    r = gate.resolve("c1", "yes")
    assert r.kind == "expired"
    assert calls["executed"] == 0


def test_has_live_pending_true_while_fresh_and_false_plus_pruned_when_expired():
    action, _ = _recording_action()
    now = {"t": 1000.0}
    gate = ActionGate(clock=lambda: now["t"], expiry_seconds=900)
    gate.propose("c1", action, title="Call")
    assert gate.has_live_pending("c1") is True          # fresh -> live
    now["t"] = 1000.0 + 901
    assert gate.has_live_pending("c1") is False          # expired -> not live (so it can't block)
    assert gate.has_pending("c1") is False               # and the stale record is pruned


def test_has_live_pending_false_when_nothing_pending():
    assert ActionGate().has_live_pending("c1") is False


def test_has_pending_stays_membership_only_so_resolve_still_reports_expiry():
    """has_pending must NOT prune, so a late reply through resolve() still gets the expiry notice."""
    action, _ = _recording_action()
    now = {"t": 1000.0}
    gate = ActionGate(clock=lambda: now["t"], expiry_seconds=900)
    gate.propose("c1", action, title="Call")
    now["t"] = 1000.0 + 901
    assert gate.has_pending("c1") is True                 # record still there
    assert gate.resolve("c1", "yes").kind == "expired"    # resolve reports it expired


def test_has_live_pending_audits_the_expiry(tmp_path):
    from agent_access.audit import AuditLog
    audit = AuditLog(tmp_path / "audit.log", clock=lambda: 1.0)
    action, _ = _recording_action()
    now = {"t": 1000.0}
    gate = ActionGate(audit=audit, clock=lambda: now["t"], expiry_seconds=900)
    gate.propose("c1", action, title="Call")
    now["t"] = 1000.0 + 901
    gate.has_live_pending("c1")
    assert "action_expired" in [e["event"] for e in audit.entries()]


def test_classify_strict_rejects_a_bare_yes_but_accepts_explicit_approval():
    """Proactive (strict) proposals need an explicit APPROVE, not a bare 'yes' that might have
    been meant for an unrelated part of the conversation. Reactive (non-strict) is unchanged."""
    assert _classify("yes", strict=True) == "unclear"
    assert _classify("sure", strict=True) == "unclear"
    assert _classify("approve", strict=True) == "approve"
    assert _classify("send it", strict=True) == "approve"
    assert _classify("yes", strict=False) == "approve"        # reactive quick-yes intact
    assert _classify("no", strict=True) == "cancel"           # cancel/edit unaffected by strict


def test_strict_proposal_needs_explicit_approval_to_execute():
    action, calls = _recording_action()
    gate = ActionGate()
    gate.propose("c1", action, strict=True, title="Reply")
    assert gate.resolve("c1", "yes").kind == "unclear"        # bare yes does NOT fire it
    assert calls["executed"] == 0
    assert gate.resolve("c1", "approve").kind == "done"       # explicit does
    assert calls["executed"] == 1


def test_reactive_proposal_still_accepts_a_quick_yes():
    action, calls = _recording_action()
    gate = ActionGate()
    gate.propose("c1", action, title="Call")                  # default: not strict
    assert gate.resolve("c1", "yes").kind == "done"
    assert calls["executed"] == 1


def test_execute_retries_once_then_succeeds():
    action, calls = _recording_action(fail_times=1)  # first execute raises, second works
    gate = ActionGate()
    gate.propose("c1", action, title="Call")
    r = gate.resolve("c1", "yes")
    assert r.kind == "done"
    assert calls["executed"] == 2  # one retry


def test_execute_fails_twice_surfaces_error():
    action, calls = _recording_action(fail_times=2)  # always raises within 2 tries
    gate = ActionGate()
    gate.propose("c1", action, title="Call")
    r = gate.resolve("c1", "yes")
    assert r.kind == "failed" and "backend down" in r.error
    assert calls["executed"] == 2


def test_classify_edit_beats_a_soft_no_or_a_yes():
    """An edit request must read as edit, not cancel, even when it carries a 'no'/'yes'."""
    assert _classify("no, change it to 3pm") == "edit"
    assert _classify("yes but reword the opening") == "edit"
    assert _classify("update the content to mention the deadline") == "edit"
    assert _classify("make it shorter") == "edit"


def test_classify_explicit_cancel_still_wins_over_edit():
    assert _classify("change of plans, cancel it") == "cancel"


def test_classify_plain_no_cancels_and_plain_yes_approves():
    assert _classify("no") == "cancel"
    assert _classify("yes") == "approve"
    assert _classify("don't send it") == "cancel"          # 'send it' must NOT approve here


def test_edit_with_a_negative_reprocesses_instead_of_cancelling():
    action, calls = _recording_action()
    gate = ActionGate()
    gate.propose("c1", action, title="Call")
    r = gate.resolve("c1", "no, change it to 4pm")
    assert r.kind == "edit" and r.reprocess_text == "no, change it to 4pm"
    assert calls["executed"] == 0


def test_edit_reply_reprocesses():
    action, _ = _recording_action()
    gate = ActionGate()
    gate.propose("c1", action, title="Call")
    r = gate.resolve("c1", "make it 4pm instead")
    assert r.kind == "edit" and r.reprocess_text == "make it 4pm instead"
    assert gate.resolve("c1", "yes").kind == "none"  # old proposal dropped


def test_second_unclear_gives_up_and_reprocesses():
    action, calls = _recording_action()
    gate = ActionGate()
    gate.propose("c1", action, title="Call")
    assert gate.resolve("c1", "hmm").kind == "unclear"          # first: re-ask
    r = gate.resolve("c1", "tell me a joke")                    # second: give up
    assert r.kind == "none"
    assert calls["executed"] == 0


def test_pending_is_scoped_per_conversation():
    action, calls = _recording_action()
    gate = ActionGate()
    gate.propose("A", action, title="Call")
    assert gate.resolve("B", "yes").kind == "none"  # B has nothing pending
    assert calls["executed"] == 0
    assert gate.resolve("A", "yes").kind == "done"  # A still pending


def test_lifecycle_is_audited(tmp_path):
    audit = AuditLog(tmp_path / "audit.log", clock=lambda: 1.0)
    action, _ = _recording_action()
    gate = ActionGate(audit=audit)
    gate.propose("c1", action, title="Call")
    gate.resolve("c1", "yes")
    events = [e["event"] for e in audit.entries()]
    assert "action_proposed" in events and "action_executed" in events
