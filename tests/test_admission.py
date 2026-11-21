"""Admission rules, including the local /health command."""

from datetime import UTC, datetime

from peyda_pm_bot.domain.conversations.admission import admit, is_health_command
from peyda_pm_bot.domain.conversations.models import Admission, Allowlist, UpdateKind
from tests.conftest import NOW, incoming


def test_identified_member_in_the_topic_is_accepted() -> None:
    allowlist = Allowlist(group_id=-100, topic_id=7, admin_ids=frozenset({5}))
    decision = admit(incoming(sender_id=8), allowlist)
    assert decision.admission is Admission.ACCEPTED
    assert decision.reason is None


def test_unknown_sender_and_wrong_place_are_ignored() -> None:
    allowlist = Allowlist(group_id=-100, topic_id=7, admin_ids=frozenset({5}))
    cases = [
        (incoming(kind=UpdateKind.OTHER, text="secret"), "unsupported"),
        (incoming(chat_id=None, message_id=None), "unsupported"),
        (incoming(chat_id=-200), "outside_chat"),
        (incoming(topic_id=9), "outside_topic"),
        (incoming(topic_id=None), "outside_topic"),
        (incoming(sender_id=None), "anonymous"),
        (incoming(sender_id=0), "anonymous"),
    ]
    for update, reason in cases:
        decision = admit(update, allowlist)
        assert decision.admission is Admission.IGNORED
        assert decision.reason == reason


def test_health_is_only_an_exact_admin_message() -> None:
    allowlist = Allowlist(group_id=-100, topic_id=7, admin_ids=frozenset({5}))
    assert is_health_command(incoming(text="/health"), allowlist)
    rejected = [
        incoming(text="/health", sender_id=8),
        incoming(text="/health", kind=UpdateKind.EDITED_MESSAGE),
        incoming(text="/health "),
        incoming(text="/health@bot"),
        incoming(text=" /health"),
        incoming(text="/Health"),
        incoming(text="/health", chat_id=-200),
    ]
    assert all(not is_health_command(update, allowlist) for update in rejected)


def test_naive_timestamp_is_not_a_domain_fact() -> None:
    naive = datetime(2026, 11, 21, 12, 0)
    update = incoming(message_at=naive)
    assert update.message_at == naive
    assert NOW.tzinfo is UTC
