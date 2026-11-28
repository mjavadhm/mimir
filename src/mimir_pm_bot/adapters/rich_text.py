"""Native Telegram rich messages for agent Markdown and literal status output."""

import unicodedata

from aiogram.types import InputRichBlockParagraph, InputRichMessage


def rich_text(text: str, *, literal: bool = False) -> InputRichMessage:
    rtl = any(unicodedata.bidirectional(char) in {"R", "AL"} for char in text)
    if literal:
        return InputRichMessage(blocks=[InputRichBlockParagraph(text=text)], is_rtl=rtl)
    return InputRichMessage(markdown=text, is_rtl=rtl)
