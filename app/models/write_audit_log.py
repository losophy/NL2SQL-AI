"""
`write_audit_log` ORM 模型

定义元数据库中 write_audit_log 表对应的 ORM 模型
负责保存写操作审计日志：操作类型、SQL、执行前快照（回滚依据）与状态
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.mysql import BIGINT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WriteAuditLogMySQL(Base):
    """写操作审计日志表对应的 ORM 模型"""

    __tablename__ = "write_audit_log"

    log_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), primary_key=True, autoincrement=True, comment="审计记录ID"
    )
    session_id: Mapped[str | None] = mapped_column(
        String(64), comment="会话ID(可能为空，兼容无会话兜底场景)"
    )
    seq: Mapped[int] = mapped_column(
        Integer, default=0, comment="同一会话内的操作序号(单调递增,用于逆序回滚)"
    )
    op_type: Mapped[str] = mapped_column(String(16), comment="操作类型(INSERT/UPDATE/DELETE)")
    table_name: Mapped[str] = mapped_column(String(128), comment="受影响表")
    sql_text: Mapped[str] = mapped_column(Text, comment="实际执行的SQL")
    before_data: Mapped[list | None] = mapped_column(
        JSON, comment="执行前的受影响行快照(回滚依据)"
    )
    row_count: Mapped[int] = mapped_column(Integer, default=0, comment="影响行数")
    status: Mapped[str] = mapped_column(
        String(16), default="committed", comment="committed=已执行 / rolled_back=已回滚"
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.now(), comment="执行时间"
    )
