"""
SQL 执行节点

负责执行最终 SQL，并记录查询结果。
它是当前 SQL 闭环的结束节点，执行完成后流程进入 END。
"""

import re

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
        if sql_type in ("insert", "update", "delete"):
            # 写操作：已通过人工审批，执行后展示受影响表的内容，而非"影响行数"
            affected = await dw_mysql_repository.run_mutation(sql)
            result = await _fetch_table_after_write(
                dw_mysql_repository, sql, sql_type, affected
            )
        else:
            result = await dw_mysql_repository.run(sql)
        logger.info(f"SQL执行结果：{result}")
        writer({"type": "progress", "step": step, "status": "success"})
        # 把最终执行的 SQL 一并带在 result 事件里，前端可以在结果表格前展示
        writer({"type": "result", "data": result, "sql": sql})

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
