from pydantic import Field

from mimir_pm_bot.application.memory import Positive, Request, Text


class Review(Request):
    source: Positive
    pull: Positive | str
    focus: Text | None = None
    context: Text | None = None
    publish: bool = False


class ReviewAction(Request):
    source: Positive
    review_id: int = Field(gt=0)
