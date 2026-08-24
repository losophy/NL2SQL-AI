"""
SQL 类型识别节点

负责判断生成的 SQL 是查询（SELECT）还是写操作（INSERT/UPDATE/DELETE）。
SELECT 直接进入执行；写操作会继续走影响预估与人工审批，保证数据安全。
"""

import re

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger

# 匹配 SQL 开头的操作关键字（支持 WITH 前缀的 SELECT 以及 REPLACE 等）
_SQL_TYPE_RE = re.compile(r"^\s*(?:WITH\s+[\w]+\s+AS\s*\([^)]*\)\s*)*\s*(SELECT|INSERT|UPDATE|DELETE|REPLACE)\b", re.IGNORECASE)


def classify_sql_type(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """识别 SQL 操作类型并写入状态"""

    writer = runtime.stream_writer
    step = "识别SQL类型"
    writer({"type": "progress", "step": step, "status": "running"})

    sql = state.get("sql") or ""
    match = _SQL_TYPE_RE.search(sql)
    raw = match.group(1).lower() if match else "select"

    # REPLACE 在 MySQL 中属于写入语义，归入 insert 类审批
    sql_type = {"insert": "insert", "replace": "insert"}.get(raw, raw)

    logger.info(f"识别SQL类型：{sql_type} <- {sql[:80]}")
    writer({"type": "progress", "step": step, "status": "success"})
    return {"sql_type": sql_type}
