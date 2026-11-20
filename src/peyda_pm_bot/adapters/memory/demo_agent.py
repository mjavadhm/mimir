"""Offline script exercising the public memory API, not semantic intelligence."""

from collections.abc import AsyncIterator
from dataclasses import replace

from peyda_pm_bot.adapters.antigravity.fake import FakeAgent
from peyda_pm_bot.application.memory.service import MemoryService
from peyda_pm_bot.domain.agent.models import AgentEvent, AgentRequest, AgentResult
from peyda_pm_bot.domain.memory.models import Evidence, MemoryRecord, MemoryView


class MemoryDemoAgent(FakeAgent):
    def __init__(self) -> None:
        super().__init__()
        self.memory: MemoryService | None = None

    async def run(self, request: AgentRequest) -> AsyncIterator[AgentEvent | AgentResult]:
        if self.memory is None or request.memory_token is None:
            raise ValueError("Demo memory not wired")
        token = request.memory_token
        text = request.messages[-1].message.text if request.messages[-1].message else ""
        source = request.messages[-1].update_id
        if text == "ثبت تصمیم ورود":
            case = await self.memory.prepare(
                token,
                "case",
                MemoryRecord("case", "ورود", aliases=("login",), source_update_ids=(source,)),
            )
            await self.memory.prepare(
                token,
                "decision",
                MemoryRecord(
                    "note",
                    "روش ورود",
                    "ورود با شماره تلفن",
                    category="decision",
                    case_ids=(case.record.id,),
                    source_update_ids=(source,),
                ),
            )
            yield AgentResult("silent")
        elif text in {"بازیابی تصمیم ورود", "اصلاح تصمیم ورود"}:
            found = await self.memory.search(token, "روش ورود")
            note = found.items[0]
            if not isinstance(note, MemoryView):
                raise ValueError("Expected a memory record")
            if text == "اصلاح تصمیم ورود":
                await self.memory.prepare(
                    token,
                    "correct",
                    replace(
                        note.record,
                        text="ورود با ایمیل",
                        source_update_ids=(source,),
                        reason="شرایط تغییر کرد و اصلاح شد",
                    ),
                    note.record.revision,
                )
                yield AgentResult("silent")
            else:
                evidence = await self.memory.sources(token, note.record.source_update_ids)
                source_item = evidence.items[0]
                if not isinstance(source_item, Evidence):
                    raise ValueError("Expected source evidence")
                yield AgentResult("reply", f"{note.record.text}؛ منبع: {source_item.update_id}")
        else:
            yield AgentResult("silent")
