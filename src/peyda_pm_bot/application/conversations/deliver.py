"""Turn a pending health notice into one stored result. The network call is the sender."""

from pydantic import BaseModel, ConfigDict

from peyda_pm_bot.application.conversations.ingest import health_attempt_cap
from peyda_pm_bot.application.conversations.ports import (
    DeliveryRejected,
    DeliveryRetry,
    DeliveryUnknown,
    HealthNotice,
    HealthSender,
)


class HealthResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    update_id: int
    status: str
    external_message_id: int | None
    attempt_count: int


async def deliver_one(
    notice: HealthNotice,
    sender: HealthSender,
    chat_id: int,
    topic_id: int,
) -> HealthResult:
    """Send one health reply. An unknown result stays unknown instead of being resent."""

    attempts = notice.attempt_count + 1
    try:
        external_id = await sender.send_health(chat_id, topic_id)
    except DeliveryRetry:
        status = "pending" if attempts < health_attempt_cap() else "rejected"
        return HealthResult(
            update_id=notice.update_id,
            status=status,
            external_message_id=None,
            attempt_count=attempts,
        )
    except DeliveryUnknown:
        return HealthResult(
            update_id=notice.update_id,
            status="unknown",
            external_message_id=None,
            attempt_count=attempts,
        )
    except DeliveryRejected:
        return HealthResult(
            update_id=notice.update_id,
            status="rejected",
            external_message_id=None,
            attempt_count=attempts,
        )
    return HealthResult(
        update_id=notice.update_id,
        status="sent",
        external_message_id=external_id,
        attempt_count=attempts,
    )
