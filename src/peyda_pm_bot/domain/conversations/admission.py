"""Decide which updates enter the product record and which one asks for /health."""

from peyda_pm_bot.domain.conversations.models import (
    HEALTH_TEXT,
    Admission,
    AdmissionDecision,
    Allowlist,
    IncomingUpdate,
    UpdateKind,
)


def admit(update: IncomingUpdate, allowlist: Allowlist) -> AdmissionDecision:
    """Admit an identified message in the configured topic. Do not persist other text."""

    if update.kind is UpdateKind.OTHER or update.chat_id is None or update.message_id is None:
        return AdmissionDecision(admission=Admission.IGNORED, reason="unsupported")
    if update.chat_id != allowlist.group_id:
        return AdmissionDecision(admission=Admission.IGNORED, reason="outside_chat")
    if update.topic_id != allowlist.topic_id:
        return AdmissionDecision(admission=Admission.IGNORED, reason="outside_topic")
    if update.sender_id is None or update.sender_id <= 0:
        return AdmissionDecision(admission=Admission.IGNORED, reason="anonymous")
    return AdmissionDecision(admission=Admission.ACCEPTED, reason=None)


def is_health_command(update: IncomingUpdate, allowlist: Allowlist) -> bool:
    """True only for an original `/health` from an allowlisted admin.

    Edited messages and a `@bot` suffix are not a health command.
    """

    decision = admit(update, allowlist)
    if decision.admission is not Admission.ACCEPTED:
        return False
    if update.kind is not UpdateKind.MESSAGE:
        return False
    if update.sender_id is None or not allowlist.accepts_admin(update.sender_id):
        return False
    return update.text == HEALTH_TEXT
