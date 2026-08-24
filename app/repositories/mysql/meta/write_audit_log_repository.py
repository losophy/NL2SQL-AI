"""
写操作审计日志 MySQL 仓储

负责 write_audit_log 表的增查与状态更新，支撑 Time-Travel 数据回滚：
- run_sql 执行写操作成功后落库一条审计记录（含执行前快照 before_data）
- 回滚服务按会话读取审计记录，逆序还原目标操作及其之后的所有操作
"""

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.write_audit_log import WriteAuditLogMySQL


class WriteAuditLogRepository:
    """负责把写操作审计日志持久化到 Meta MySQL"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def next_seq(self, session_id: str | None) -> int:
        """返回该会话下一个操作序号（当前最大值 + 1；无记录从 1 开始）"""
        result = await self.session.execute(
            select(func.coalesce(func.max(WriteAuditLogMySQL.seq), 0)).where(
                WriteAuditLogMySQL.session_id == session_id
            )
        )
        return int(result.scalar() or 0) + 1

    async def insert_audit_log(
        self,
        *,
        session_id: str | None,
        seq: int,
        op_type: str,
        table_name: str,
        sql_text: str,
        before_data: list | None,
        row_count: int,
    ) -> int:
        """写入一条审计记录，返回自增 log_id"""
        record = WriteAuditLogMySQL(
            session_id=session_id,
            seq=seq,
            op_type=op_type.upper(),
            table_name=table_name,
            sql_text=sql_text,
            before_data=before_data,
            row_count=row_count,
            status="committed",
        )
        self.session.add(record)
        await self.session.commit()
        # commit 后从会话刷新自增主键
        await self.session.refresh(record)
        return int(record.log_id)

    async def list_audit_logs(self, session_id: str) -> list[WriteAuditLogMySQL]:
        """按 seq 正序返回某会话的全部审计记录（含已回滚的）"""
        result = await self.session.execute(
            select(WriteAuditLogMySQL)
            .where(WriteAuditLogMySQL.session_id == session_id)
            .order_by(WriteAuditLogMySQL.seq.asc())
        )
        return list(result.scalars().all())

    async def get_audit_log(self, log_id: int) -> WriteAuditLogMySQL | None:
        """按 log_id 查询单条审计记录"""
        return await self.session.get(WriteAuditLogMySQL, log_id)

    async def list_committed_after(
        self, session_id: str, seq: int
    ) -> list[WriteAuditLogMySQL]:
        """返回某会话中序号大于等于目标序号且仍处于 committed 的记录（用于 LIFO 逆序回滚）"""
        result = await self.session.execute(
            select(WriteAuditLogMySQL)
            .where(
                WriteAuditLogMySQL.session_id == session_id,
                WriteAuditLogMySQL.seq >= seq,
                WriteAuditLogMySQL.status == "committed",
            )
            .order_by(WriteAuditLogMySQL.seq.desc())
        )
        return list(result.scalars().all())

    async def mark_rolled_back(self, log_ids: list[int]):
        """把一组审计记录标记为已回滚"""
        if not log_ids:
            return
        await self.session.execute(
            update(WriteAuditLogMySQL)
            .where(WriteAuditLogMySQL.log_id.in_(log_ids))
            .values(status="rolled_back")
        )
        await self.session.commit()
