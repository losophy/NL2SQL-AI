"""
写操作影响范围预估节点

负责在写操作（INSERT/UPDATE/DELETE）执行前预估影响行数：
- INSERT 固定新增 1 行
- UPDATE/DELETE 通过 COUNT 统计 WHERE 条件命中的行数，无 WHERE 时按全表提示

预估结果写入 impact_summary，展示在人工审核卡片上供用户决策。
"""

import re

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger

# 提取 UPDATE/DELETE 的目标表名与 WHERE 子句
_UPDATE_RE = re.compile(r"UPDATE\s+([`\w]+)\s+SET\b", re.IGNORECASE)
_DELETE_RE = re.compile(r"DELETE\s+FROM\s+([`\w]+)\b", re.IGNORECASE)
_WHERE_RE = re.compile(r"\bWHERE\b(.+)$", re.IGNORECASE | re.DOTALL)


def _parse_target(sql: str) -> tuple[str, str | None]:
    """从 UPDATE/DELETE 语句中提取 (表名, WHERE子句)；无 WHERE 时返回 None"""
    sql = sql.strip().rstrip(";")
    match = _UPDATE_RE.search(sql) or _DELETE_RE.search(sql)
    if not match:
        return "", None
    table = match.group(1).strip("`")
    where = _WHERE_RE.search(sql)
    return table, (where.group(1).strip() if where else None)


async def estimate_impact(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """预估写操作影响范围并写入状态"""

    writer = runtime.stream_writer
    step = "预估影响范围"
    writer({"type": "progress", "step": step, "status": "running"})

    sql_type = state.get("sql_type", "select")
    sql = state.get("sql") or ""
    try:
        if sql_type == "insert":
            impact_summary = "新增 1 行"
            pk_note = state.get("pk_note")
            if pk_note:
                impact_summary += f"（{pk_note}）"
        else:
            table, where = _parse_target(sql)
            if not table:
                impact_summary = "未能识别目标表，请人工核对"
            else:
                count_sql = (
                    f"SELECT COUNT(*) AS cnt FROM `{table}` WHERE {where}"
                    if where
                    else f"SELECT COUNT(*) AS cnt FROM `{table}`"
                )
                rows = await runtime.context["dw_mysql_repository"].run(count_sql)
                cnt = rows[0].get("cnt", 0) if rows else 0
                verb = "更新" if sql_type == "update" else "删除"
                impact_summary = f"将{verb} {cnt} 行"
                if where is None:
                    impact_summary += "（全表操作！）"
        logger.info(f"影响预估：{impact_summary}")
        writer({"type": "progress", "step": step, "status": "success"})
        return {"impact_summary": impact_summary}

    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        # 预估失败不阻断审批流程，提示人工核对
        return {"impact_summary": "影响范围预估失败，请人工核对"}
