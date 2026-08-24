"""
会话历史 MySQL 仓储

负责把会话和消息业务实体持久化到 Meta MySQL
会话与消息的增删查都在这一层完成，Service 层负责业务编排
"""

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.chat_message import ChatMessage
from app.entities.chat_session import ChatSession
from app.models.chat_message import ChatMessageMySQL
from app.models.chat_session import ChatSessionMySQL


class ChatSessionRepository:
    """负责把会话历史业务实体持久化到 Meta MySQL"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_session(self, chat_session: ChatSession) -> ChatSession:
        """创建一个新会话并落库"""
        self.session.add(
            ChatSessionMySQL(
                id=chat_session.id,
                title=chat_session.title,
                created_at=chat_session.created_at,
                updated_at=chat_session.updated_at,
            )
        )
        await self.session.commit()
        return chat_session

    async def update_session_meta(
        self, session_id: str, title: str | None, updated_at: int
    ):
        """更新会话标题与更新时间（首条消息后回填标题、每次消息后刷新时间）

        title 为 None 时只刷新 updated_at，避免覆盖已回填的标题
        """
        values: dict = {"updated_at": updated_at}
        if title is not None:
            values["title"] = title
        await self.session.execute(
            update(ChatSessionMySQL)
            .where(ChatSessionMySQL.id == session_id)
            .values(**values)
        )
        await self.session.commit()

    async def list_sessions(self) -> list[ChatSession]:
        """按更新时间倒序返回全部会话"""
        result = await self.session.execute(
            select(ChatSessionMySQL).order_by(ChatSessionMySQL.updated_at.desc())
        )
        rows = result.scalars().all()
        return [
            ChatSession(
                id=row.id,
                title=row.title,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    async def get_session(self, session_id: str) -> ChatSession | None:
        """按 id 查询单个会话，不存在返回 None"""
        result = await self.session.execute(
            select(ChatSessionMySQL).where(ChatSessionMySQL.id == session_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return ChatSession(
            id=row.id,
            title=row.title,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def list_messages(self, session_id: str) -> list[ChatMessage]:
        """按创建时间正序返回会话的全部消息"""
        result = await self.session.execute(
            select(ChatMessageMySQL)
            .where(ChatMessageMySQL.session_id == session_id)
            .order_by(ChatMessageMySQL.created_at.asc())
        )
        rows = result.scalars().all()
        return [self._to_message(row) for row in rows]

    async def append_message(self, message: ChatMessage):
        """写入一条消息"""
        self.session.add(
            ChatMessageMySQL(
                id=message.id,
                session_id=message.session_id,
                role=message.role,
                content=message.content,
                steps=message.steps,
                sql=message.sql,
                result_summary=message.result_summary,
                error=message.error,
                created_at=message.created_at,
            )
        )
        await self.session.commit()

    async def delete_session(self, session_id: str):
        """级联删除会话及其全部消息（同一事务）"""
        await self.session.execute(
            delete(ChatMessageMySQL).where(ChatMessageMySQL.session_id == session_id)
        )
        await self.session.execute(
            delete(ChatSessionMySQL).where(ChatSessionMySQL.id == session_id)
        )
        await self.session.commit()

    @staticmethod
    def _to_message(row: ChatMessageMySQL) -> ChatMessage:
        return ChatMessage(
            id=row.id,
            session_id=row.session_id,
            role=row.role,
            content=row.content or "",
            steps=row.steps,
            sql=row.sql,
            result_summary=row.result_summary,
            error=row.error,
            created_at=row.created_at,
        )
