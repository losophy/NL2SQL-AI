"""
SQL 校验节点

负责在真正执行查询前做两件事：
1. 枚举去重兜底：用户问题含"哪些/有哪/列举"等枚举意图、且生成的 SELECT 为单列
   无聚合的简单查询时，自动补 DISTINCT（防止把事实表重复明细当"清单"返回）；
2. 语法校验：用数据库解析一次生成的 SQL（EXPLAIN），
   校验结果不在这里决定流程走向，而是通过 state["error"] 交给 graph.py 的条件边判断
"""

import re

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository

# 用户问题中的"枚举/列举"意图词（目标是实体清单而非明细/统计）
_ENUM_INTENT_RE = re.compile(r"哪些|有哪|哪几|列举|列出|分别有哪些|有哪几种|有哪些")
# 单列简单 SELECT：SELECT <仅一列，无括号/逗号/星号> FROM ...
_SIMPLE_SINGLE_COL_SELECT_RE = re.compile(
    r"^\s*SELECT\s+(?P<col>[A-Za-z_][A-Za-z0-9_\.]*)\s+FROM\b",
    re.IGNORECASE,
)
# 命中即跳过兜底：已有去重/聚合/分组，或非纯 SELECT
_SKIP_RE = re.compile(
    r"\b(DISTINCT|GROUP\s+BY|COUNT|SUM|AVG|MAX|MIN|HAVING|UNION|INSERT|UPDATE|DELETE|CREATE|DROP)\b",
    re.IGNORECASE,
)


def _ensure_distinct_for_enum(sql: str, query: str) -> str:
    """枚举意图 + 单列简单 SELECT 时自动补 DISTINCT（保守：只改最安全的情形）

    例：问题"苏苏买过哪些道具"，SQL `SELECT item_name FROM ...` 会漏掉
    事实表中同一道具被多次购买造成的重复行；补成 `SELECT DISTINCT item_name ...`。
    只在列名前插入 DISTINCT、保留其余原文；不处理多列 / 聚合 / GROUP BY /
    子查询 / 非 SELECT，避免误伤其他语义。
    """
    if not _ENUM_INTENT_RE.search(query):
        return sql
    if _SKIP_RE.search(sql):
        return sql
    m = _SIMPLE_SINGLE_COL_SELECT_RE.match(sql)
    if not m:
        return sql
    col_start = m.start("col")
    return sql[:col_start] + "DISTINCT " + sql[col_start:]


async def validate_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """校验 SQL，并返回 error 字段控制后续条件分支"""

    writer = runtime.stream_writer
    step = "校验SQL"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        # 读取 generate_sql 或 correct_sql 写入状态的候选 SQL
        sql = state["sql"]

        # ---- 枚举去重兜底：改写后写回状态，后续按新 SQL 执行 ----
        new_sql = _ensure_distinct_for_enum(sql, state.get("query") or "")
        if new_sql != sql:
            logger.info(f"枚举去重兜底：{sql} -> {new_sql}")
            writer({"type": "progress", "step": "去重规范（枚举清单）", "status": "success"})
            sql = new_sql

        # SQL 可用性必须交给真实数仓判断，这里从运行时上下文取 DW Repository
        dw_mysql_repository: DWMySQLRepository = runtime.context["dw_mysql_repository"]

        try:
            # validate 内部使用 explain <sql>，只关心数据库能否成功解析这条 SQL
            await dw_mysql_repository.validate(sql)
            writer({"type": "progress", "step": step, "status": "success"})
            logger.info("SQL语法正确")
            return {"error": None, "sql": sql}
        except Exception as e:
            # 不抛出异常中断图执行，而是把错误写入状态，供条件分支进入 correct_sql
            logger.info(f"SQL语法错误：{str(e)}")
            writer({"type": "progress", "step": step, "status": "success"})
            return {"error": str(e), "sql": sql}

    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise
