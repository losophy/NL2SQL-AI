"""
Time-Travel 数据回滚服务

基于 write_audit_log 审计日志实现写操作的还原：
- 不能靠"逆推 SQL"还原（DELETE/UPDATE 会丢失旧值），还原依据是执行前的
  before_data 快照；
- 还原必须逆序（LIFO）连带回滚目标操作之后的所有操作，保证数据一致性：
  - INSERT 撤销：解析最终执行 SQL 的主键值 → DELETE（before_data 为空）
  - UPDATE 还原：按主键把列 UPDATE 回 before_data 旧值
  - DELETE 恢复：用 before_data 逐行重新 INSERT
"""

import re

from sqlalchemy import text

from app.core.log import logger
from app.models.write_audit_log import WriteAuditLogMySQL
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta.write_audit_log_repository import (
    WriteAuditLogRepository,
)

# 解析 INSERT 的表名、列清单与 VALUES 段（与 resolve_insert_primary_key 保持一致）
_INSERT_RE = re.compile(
    r"\b(?:INSERT\s+(?:IGNORE\s+)?INTO|REPLACE\s+INTO)\s+([`\w]+)\s*(?:\(([^)]*)\))?\s*VALUES\s*(.+)",
    re.IGNORECASE | re.DOTALL,
)
_VALUES_ROW_RE = re.compile(r"\(([^)]*)\)")


def _split_commas(s: str) -> list[str]:
    """按逗号切分字段，忽略单双引号内部出现的逗号"""
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    for ch in s:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == ",":
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())
    return parts


def _unquote(v: str) -> str:
    """去掉值首尾的单双引号"""
    v = v.strip()
    if len(v) >= 2 and v[0] in ("'", '"') and v[-1] == v[0]:
        return v[1:-1]
    return v


def _literal_sql(v) -> str:
    """把 Python 值转成 SQL 字面量（用于日志展示；实际执行统一走绑定参数）"""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"


class RollbackService:
    """负责审计日志查询与写操作还原"""

    def __init__(
        self,
        audit_repository: WriteAuditLogRepository,
        dw_mysql_repository: DWMySQLRepository,
    ):
        self.audit_repository = audit_repository
        self.dw_mysql_repository = dw_mysql_repository

    # ------------------------------------------------------------------ 查询

    async def list_audit_logs(self, session_id: str) -> list[dict]:
        """返回某会话的全部写操作审计记录（按执行顺序正序）"""
        logs = await self.audit_repository.list_audit_logs(session_id)
        return [self._to_dict(log) for log in logs]

    @staticmethod
    def _to_dict(log: WriteAuditLogMySQL) -> dict:
        return {
            "log_id": log.log_id,
            "session_id": log.session_id,
            "seq": log.seq,
            "op_type": log.op_type,
            "table_name": log.table_name,
            "sql_text": log.sql_text,
            "before_data": log.before_data,
            "row_count": log.row_count,
            "status": log.status,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }

    # ------------------------------------------------------------------ 回滚

    async def rollback(self, log_id: int) -> dict:
        """回滚目标审计记录及其之后的所有操作（LIFO 逆序），返回回滚摘要

        - 目标操作之后已回滚过的操作不会重复处理（仅取 committed）；
        - 任意一步失败则整体回滚失败并抛出异常，审计状态保持不变。
        """
        target = await self.audit_repository.get_audit_log(log_id)
        if target is None:
            raise ValueError(f"审计记录不存在：log_id={log_id}")
        if target.status == "rolled_back":
            raise ValueError(f"该操作已回滚，无需重复回滚（log_id={log_id}）")
        if not target.session_id:
            raise ValueError("该操作没有归属会话，无法按顺序回滚，请人工核对")

        # 目标 + 之后所有 committed 操作，按 seq 倒序（LIFO）
        logs = await self.audit_repository.list_committed_after(
            target.session_id, target.seq
        )
        if not logs:
            raise ValueError("未找到可回滚的操作记录")

        descriptions: list[str] = []
        restored_rows = 0

        # 清理可能残留的只读事务，本次回滚在同一个干净事务内执行
        await self.dw_mysql_repository.session.rollback()
        try:
            for log in logs:
                if log.op_type == "INSERT":
                    rows = await self._revert_insert(log)
                    descriptions.append(
                        f"撤销插入：{log.table_name} 删除 {rows} 行"
                    )
                elif log.op_type == "UPDATE":
                    rows = await self._revert_update(log)
                    descriptions.append(
                        f"还原更新：{log.table_name} 恢复 {rows} 行"
                    )
                elif log.op_type == "DELETE":
                    rows = await self._revert_delete(log)
                    descriptions.append(
                        f"恢复删除：{log.table_name} 重新插入 {rows} 行"
                    )
                else:
                    logger.warning(f"跳过未知操作类型：{log.op_type}（log_id={log.log_id}）")
                    continue
                restored_rows += rows
            await self.dw_mysql_repository.session.commit()
        except Exception as e:
            # 还原失败：回滚事务，审计状态保持不变，等待人工介入
            await self.dw_mysql_repository.session.rollback()
            logger.error(f"回滚失败（log_id={log_id}）：{e}")
            raise ValueError(f"回滚执行失败，数据未变更：{e}") from e

        rolled_ids = [log.log_id for log in logs]
        await self.audit_repository.mark_rolled_back(rolled_ids)
        return {
            "log_id": log_id,
            "rolled_back": len(rolled_ids),
            "restored_rows": restored_rows,
            "descriptions": descriptions,
            "rolled_back_log_ids": rolled_ids,
        }

    # ------------------------------------------------------------------ 各类还原

    async def _revert_delete(self, log: WriteAuditLogMySQL) -> int:
        """DELETE 恢复：用 before_data 快照逐行重新 INSERT"""
        rows = log.before_data or []
        if not rows:
            return 0
        table = log.table_name
        for row in rows:
            cols = [c for c in row.keys() if c in row]
            if not cols:
                continue
            col_sql = ", ".join(f"`{c}`" for c in cols)
            param_sql = ", ".join(f":v{i}" for i in range(len(cols)))
            params = {f"v{i}": row[c] for i, c in enumerate(cols)}
            await self.dw_mysql_repository.session.execute(
                text(
                    f"INSERT INTO `{table}` ({col_sql}) VALUES ({param_sql})"
                ),
                params,
            )
        return len(rows)

    async def _revert_update(self, log: WriteAuditLogMySQL) -> int:
        """UPDATE 还原：按主键把列值改回 before_data 中的旧值"""
        rows = log.before_data or []
        if not rows:
            return 0
        table = log.table_name
        pk_cols = await self.dw_mysql_repository.get_primary_keys(table)
        if not pk_cols:
            raise ValueError(f"表 {table} 无主键，无法精确还原 UPDATE，请人工核对")
        count = 0
        for row in rows:
            set_cols = [c for c in row.keys() if c not in pk_cols and c in row]
            if not set_cols:
                continue
            set_sql = ", ".join(f"`{c}` = :s{i}" for i, c in enumerate(set_cols))
            where_sql = " AND ".join(f"`{c}` = :w{i}" for i, c in enumerate(pk_cols))
            params = {f"s{i}": row[c] for i, c in enumerate(set_cols)}
            params.update({f"w{i}": row[c] for i, c in enumerate(pk_cols)})
            await self.dw_mysql_repository.session.execute(
                text(f"UPDATE `{table}` SET {set_sql} WHERE {where_sql}"), params
            )
            count += 1
        return count

    async def _revert_insert(self, log: WriteAuditLogMySQL) -> int:
        """INSERT 撤销：解析最终执行 SQL 中的主键值，按主键删除新插入的行"""
        table = log.table_name
        pk_cols = await self.dw_mysql_repository.get_primary_keys(table)
        if not pk_cols:
            raise ValueError(f"表 {table} 无主键，无法撤销 INSERT，请人工核对")
        pk = pk_cols[0]

        match = _INSERT_RE.search(log.sql_text)
        if not match:
            raise ValueError("无法解析 INSERT 语句，无法撤销，请人工核对")
        cols_raw = match.group(2)
        if cols_raw:
            cols = [c.strip().strip("`") for c in _split_commas(cols_raw)]
            rows_raw = _VALUES_ROW_RE.findall(match.group(3))
            if pk not in cols:
                raise ValueError(
                    f"INSERT 未包含主键列 {pk}，无法确定插入行，请人工核对"
                )
            idx = cols.index(pk)
            pk_values = [_unquote(r[idx]) for r in (_split_commas(rg) for rg in rows_raw)]
        else:
            # 无列清单：按 SHOW COLUMNS 顺序取主键位置
            col_map = await self.dw_mysql_repository.get_column_types(table)
            ordered_cols = list(col_map.keys())
            if pk not in ordered_cols:
                raise ValueError(f"表 {table} 无主键列 {pk}，无法撤销，请人工核对")
            idx = ordered_cols.index(pk)
            rows_raw = _VALUES_ROW_RE.findall(match.group(3))
            pk_values = []
            for rg in rows_raw:
                vals = _split_commas(rg)
                if idx < len(vals):
                    pk_values.append(_unquote(vals[idx]))
        if not pk_values:
            return 0

        params = {f"p{i}": v for i, v in enumerate(pk_values)}
        placeholders = ", ".join(f":p{i}" for i in range(len(pk_values)))
        await self.dw_mysql_repository.session.execute(
            text(f"DELETE FROM `{table}` WHERE `{pk}` IN ({placeholders})"), params
        )
        return len(pk_values)
