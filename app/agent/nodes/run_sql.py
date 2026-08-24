"""
SQL 执行节点

负责执行最终 SQL，并记录查询结果。
它是当前 SQL 闭环的结束节点，执行完成后流程进入 END。
"""

import datetime as _dt
import re
from decimal import Decimal

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger

# 按写操作类型提取目标表名（INSERT 兼容 INSERT IGNORE / REPLACE 前缀）
_WRITE_TABLE_RE = {
    "insert": re.compile(
        r"\b(?:INSERT\s+(?:IGNORE\s+)?INTO|REPLACE\s+INTO)\s+([`\w]+)", re.IGNORECASE
    ),
    "update": re.compile(r"\bUPDATE\s+([`\w]+)\s+SET\b", re.IGNORECASE),
    "delete": re.compile(r"\bDELETE\s+FROM\s+([`\w]+)\b", re.IGNORECASE),
}


def _extract_write_table(sql: str, sql_type: str) -> str | None:
    """从写操作 SQL 中提取目标表名；解析失败返回 None"""
    table_re = _WRITE_TABLE_RE.get(sql_type)
    if not table_re:
        return None
    match = table_re.search(sql)
    return match.group(1).strip("`") if match else None


def _json_safe(value):
    """把 Decimal / datetime 等非 JSON 类型转成可落 JSON 列的值"""
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    return value


async def _write_audit_log(
    runtime: Runtime[DataAgentContext],
    state: DataAgentState,
    sql: str,
    sql_type: str,
    affected: int,
) -> int | None:
    """写操作执行成功后落库一条审计记录（执行前快照 + SQL + 影响行数）

    返回审计记录 log_id；审计仓储未注入或落库失败时返回 None，不阻断主流程。
    """
    repository = runtime.context.get("write_audit_log_repository")
    if repository is None:
        return None
    try:
        table = _extract_write_table(sql, sql_type)
        if not table:
            logger.warning("审计落库跳过：无法解析写操作目标表")
            return None
        session_id = state.get("session_id")
        seq = await repository.next_seq(session_id)
        before_data = state.get("before_data") or []
        log_id = await repository.insert_audit_log(
            session_id=session_id,
            seq=seq,
            op_type=sql_type,
            table_name=table,
            sql_text=sql,
            before_data=_json_safe(before_data),
            row_count=affected,
        )
        logger.info(f"写操作审计落库：log_id={log_id} seq={seq} {sql_type} {table} {affected} 行")
        return log_id
    except Exception as e:  # noqa: BLE001 审计失败不影响写操作主流程
        logger.error(f"写操作审计落库失败：{e}")
        # 落库失败会把 meta session 置为"待回滚"状态，若不清理会污染同请求后续的消息落库
        # （报错：This Session's transaction has been rolled back due to a previous exception）
        try:
            await repository.session.rollback()
        except Exception:  # noqa: BLE001 rollback 清理失败不阻断主流程
            pass
        return None


async def _fetch_table_after_write(
    dw_mysql_repository, sql: str, sql_type: str, affected: int
) -> list[dict]:
    """写操作成功后返回受影响表的全量数据（多表结构，前端渲染为"表名（N 行）+ 表格"）

    解析表名或查表失败时降级为"影响行数"，保证结果区始终有可展示内容。
    """
    try:
        table_re = _WRITE_TABLE_RE.get(sql_type)
        if not table_re:
            return [{"影响行数": affected}]
        match = table_re.search(sql)
        if not match:
            return [{"影响行数": affected}]
        table = match.group(1).strip("`")
        rows = await dw_mysql_repository.run(f"SELECT * FROM `{table}`")
        return [{"表名": table, "行数": len(rows), "数据": rows}]
    except Exception as e:  # noqa: BLE001 查表失败不影响主流程，降级展示影响行数
        logger.error(f"查询受影响表数据失败，降级为影响行数：{e}")
        return [{"影响行数": affected}]


async def run_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """执行 SQL 并产出最终问数结果"""

    writer = runtime.stream_writer
    step = "执行SQL"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        # 这里拿到的可能是 generate_sql 直接通过校验的 SQL，也可能是 correct_sql 覆盖后的 SQL
        sql = state["sql"]
        sql_type = state.get("sql_type", "select")
        dw_mysql_repository = runtime.context["dw_mysql_repository"]

        # 真实数据库访问统一封装在仓储层，节点只负责从状态取 SQL 并触发执行
        audit_log_id: int | None = None
        if sql_type in ("insert", "update", "delete"):
            # 写操作：已通过人工审批，执行后展示受影响表的内容，而非"影响行数"
            affected = await dw_mysql_repository.run_mutation(sql)
            # Time-Travel：执行成功后落库审计记录（含执行前快照，供后续回滚）
            audit_log_id = await _write_audit_log(runtime, state, sql, sql_type, affected)
            result = await _fetch_table_after_write(
                dw_mysql_repository, sql, sql_type, affected
            )
        else:
            result = await dw_mysql_repository.run(sql)
        logger.info(f"SQL执行结果：{result}")
        writer({"type": "progress", "step": step, "status": "success"})
        # 把最终执行的 SQL 一并带在 result 事件里，前端可以在结果表格前展示
        # audit_log_id 供前端在消息左侧挂接"回滚"入口（写操作成功且审计落库时非空）
        writer({"type": "result", "data": result, "sql": sql, "audit_log_id": audit_log_id})
        return {"audit_log_id": audit_log_id} if audit_log_id else {}

    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        message = str(e)
        # 主键/唯一键冲突（MySQL 1062）：转成可读提示，不再抛原始 traceback
        if "1062" in message or "Duplicate entry" in message:
            first_line = message.splitlines()[0]
            writer(
                {
                    "type": "error",
                    "message": (
                        f"写入失败：主键或唯一键冲突（{first_line}）。"
                        "请换一个不冲突的主键值，或重新描述需求让我重新生成。"
                    ),
                }
            )
            return {}
        # 列数与值数量不匹配（MySQL 1136）：转成可读提示
        if "1136" in message or "Column count" in message:
            first_line = message.splitlines()[0]
            writer(
                {
                    "type": "error",
                    "message": (
                        f"写入失败：列数与值数量不匹配（{first_line}）。"
                        "请补齐每一行的字段值后重试，或重新描述需求让我重新生成。"
                    ),
                }
            )
            return {}
        raise
