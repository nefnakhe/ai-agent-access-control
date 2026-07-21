"""Two-tier access control: chat allowlist (who may talk) vs action allowlist
(who may make the agent act). Action authority is always a subset of chat access."""
from agent_access.access import AccessPolicy


# --- Tier 1: chat allowlist ---

def test_none_chat_allowlist_lets_anyone_chat():
    p = AccessPolicy.from_ids(chat_allowed=None)
    assert p.may_chat("anyone") is True


def test_empty_chat_allowlist_denies_everyone():
    p = AccessPolicy.from_ids(chat_allowed=[])
    assert p.may_chat("u1") is False


def test_chat_allowlist_membership():
    p = AccessPolicy.from_ids(chat_allowed=["u1", "u2"])
    assert p.may_chat("u1") is True
    assert p.may_chat("u3") is False


# --- Tier 2: action allowlist ---

def test_unset_action_allowlist_lets_any_chat_user_act():
    """action_allowed=None => any chat-allowed user may act (single-user default)."""
    p = AccessPolicy.from_ids(chat_allowed=["u1"], action_allowed=None)
    assert p.may_act("u1") is True


def test_empty_action_allowlist_denies_all_actions():
    p = AccessPolicy.from_ids(chat_allowed=["u1"], action_allowed=[])
    assert p.may_chat("u1") is True
    assert p.may_act("u1") is False


def test_action_allowlist_membership():
    p = AccessPolicy.from_ids(chat_allowed=["ant", "assistant"], action_allowed=["ant"])
    assert p.may_act("ant") is True
    assert p.may_act("assistant") is False   # can chat, cannot act


def test_action_authority_is_subset_of_chat_access():
    """A user off the chat allowlist can never act, even if wrongly listed as an actor."""
    p = AccessPolicy.from_ids(chat_allowed=["ant"], action_allowed=["ghost"])
    assert p.may_chat("ghost") is False
    assert p.may_act("ghost") is False       # least privilege: no chat => no act


# --- Approval is an action ---

def test_approving_is_gated_like_acting():
    p = AccessPolicy.from_ids(chat_allowed=["ant", "assistant"], action_allowed=["ant"])
    assert p.may_approve("ant") is True
    assert p.may_approve("assistant") is False   # chat-only user cannot rubber-stamp a proposal


def test_ids_are_normalized():
    p = AccessPolicy.from_ids(chat_allowed=[" u1 ", "", "u2"])
    assert p.may_chat("u1") is True and p.may_chat("u2") is True
