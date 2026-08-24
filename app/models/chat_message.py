"""
`chat_message` ORM 模型

定义元数据库中 chat_message 表对应的 ORM 模型
负责保存会话内的一条条消息：用户问题与智能体回复（含执行步骤、SQL、结果摘要）
"""

from sqlalchemy import BigInteger, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ChatMessageMySQL(Base):
    """会话消息表对应的 ORM 模型"""

    __tablename__ = "chat_message"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="消息id(UUID字符串)")
    session_id: Mapped[str | None] = mapped_column(String(64), comment="所属会话id")
    role: Mapped[str | None] = mapped_column(String(16), comment="user/assistant")
    content: Mapped[str | None] = mapped_column(Text, comment="文本内容(assistant为摘要文本)")
    # 智能体回复的执行步骤进度（StepRail 数据）
    steps: Mapped[dict | list | None] = mapped_column(JSON, comment="assistant执行步骤进度")
    sql: Mapped[str | None] = mapped_column(Text, comment="最终执行的SQL")
    # 查询结果只保留前 10 行样例，避免完整结果集撑大表
    result_summary: Mapped[dict | list | None] = mapped_column(
        JSON, comment="结果摘要(前10行样例)"
    )
    error: Mapped[str | None] = mapped_column(Text, comment="错误信息")
    # 写操作执行成功后的审计记录 id：非空时前端在消息左侧展示回滚入口
    audit_log_id: Mapped[int | None] = mapped_column(BigInteger, comment="关联的写操作审计记录ID")
    created_at: Mapped[int | None] = mapped_column(BigInteger, comment="创建时间(epoch毫秒)")
