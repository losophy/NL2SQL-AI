"""
`chat_session` ORM 模型

定义元数据库中 chat_session 表对应的 ORM 模型
负责保存前端会话历史：会话 id、标题、创建与更新时间
"""

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ChatSessionMySQL(Base):
    """会话历史表对应的 ORM 模型"""

    __tablename__ = "chat_session"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="会话id(UUID字符串)")
    title: Mapped[str | None] = mapped_column(String(255), comment="会话标题(首条用户消息截断30字)")
    created_at: Mapped[int | None] = mapped_column(BigInteger, comment="创建时间(epoch毫秒)")
    updated_at: Mapped[int | None] = mapped_column(BigInteger, comment="更新时间(epoch毫秒)")
