"""Typed inputs to the subject-memory tools."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
Positive = Annotated[int, Field(gt=0)]


class Request(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    turn: Positive


class Search(Request):
    query: Short


class Read(Request):
    subject_id: Positive | None = None
    decision: Short | None = None
    history: bool = False


class Sources(Request):
    versions: list[Positive] = Field(min_length=1, max_length=32)


class Stage(Request):
    entity: Literal["decision", "subject"] = "decision"
    operation: Short
    subject: Short
    decision: Short | None = None
    expected_revision: int = Field(ge=0)
    text: Text | None = None
    reason: Text | None = None
    status: Literal["active", "inactive"] = "active"
    sources: list[Positive] = Field(min_length=1, max_length=32)
