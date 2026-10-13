"""AI 调用层基础数据结构。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ChatMessage:
    role: str          # system / user / assistant
    content: str

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content}


def sys_msg(content: str) -> ChatMessage:
    return ChatMessage("system", content)


def user_msg(content: str) -> ChatMessage:
    return ChatMessage("user", content)


@dataclass
class StreamResult:
    """一次完整调用后的汇总信息。"""

    text: str = ""
    engine: str = "mock"       # mock / real
    ai_id: str = ""
    error: str = ""
    fallback: bool = False
    meta: dict = field(default_factory=dict)
