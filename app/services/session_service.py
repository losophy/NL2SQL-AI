"""
会话历史服务

负责会话历史的业务编排：创建、列表、详情、删除
不直接感知 ORM 与 SQL，只面向仓储层提供的业务实体
"""

import time
import uuid

from app.entities.chat_message import ChatMessage
from app.entities.chat_session import ChatSession
from app.repositories.mysql.meta.chat_session_repository import ChatSessionRepository


def _now_ms() -> int:
    """当前时间戳（epoch 毫秒）"""
    return int(time.time() * 1000)


def _make_id() -> str:
    """生成消息/会话 id（UUID 无横线）"""
    return uuid.uuid4().hex


class SessionService:
    """封装会话历史的创建 查询与删除业务"""

    def __init__(self, repository: ChatSessionRepository):
        self.repository = repository

    async def create_session(self) -> ChatSession:
        """创建一个空会话"""
        now = _now_ms()
        chat_session = ChatSession(
            id=_make_id(),
            title=None,
            created_at=now,
            updated_at=now,
        )
        await self.repository.create_session(chat_session)
        return chat_session

    async def list_sessions(self) -> list[ChatSession]:
        """列出全部历史会话（更新时间倒序）"""
        return await self.repository.list_sessions()

    async def get_session_detail(
        self, session_id: str
    ) -> tuple[ChatSession, list[ChatMessage]] | None:
        """查询会话详情，不存在返回 None"""
        chat_session = await self.repository.get_session(session_id)
        if chat_session is None:
            return None
        messages = await self.repository.list_messages(session_id)
        return chat_session, messages

    async def delete_session(self, session_id: str) -> bool:
        """删除会话及其消息，会话不存在返回 False"""
        chat_session = await self.repository.get_session(session_id)
        if chat_session is None:
            return False
        await self.repository.delete_session(session_id)
        return True
