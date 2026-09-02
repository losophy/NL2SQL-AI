"""
写操作影响范围预估与安全校验节点

职责（UPDATE/DELETE 在执行与人工审批之前做双重把关）：
1. 影响预估：通过 SELECT 命中行数预估影响范围，并抓取执行前快照 before_data 供 Time-Travel 回滚；
2. 安全校验（四级），命中即拦截（不弹人工审批卡、不执行）：
   - WHERE 完全缺失；
   - WHERE 为恒真条件（1=1 / 1 / true 等，模型常用它绕过"禁止无 WHERE"约束）；
   - WHERE 命中行数 == 表内全量行数（如 `gender='男' OR gender='女'` 这类覆盖全表的条件）；
   - 用户意图含"所有/全部"等全量词，但 WHERE 用 IN 只列举一部分值且命中占比 ≥40%
     （如只写前 10 个 player_id 冒充全部——会造成静默的部分执行）。
   拦截时返回 blocked_reason 写入状态，图路由到 cancel_write 结束，前端只看到"已拦截"提示。
INSERT 固定新增 1 行，无安全校验需求，before_data 为空（回滚时按主键删除新插入的行）。
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

# 恒真 WHERE（整段条件均为永真，模型常用来绕过"禁止无 WHERE 写操作"约束）
# 覆盖：1=1 / 1 / true / 'a'='a' / 1>0 等，^$ 确保没有额外限定
_TRIVIAL_WHERE_RE = re.compile(
    r"^\s*(?:1\s*=\s*1|1\b|true\b|''\s*=\s*''|'[^']*'\s*=\s*'[^']*'|\d+\s*[<>]\s*\d+)\s*$",
    re.IGNORECASE,
)

# 用户问题中的"全量/整体"意图词（用于识别"用部分 IN 列表冒充全集"的手法）
_FULL_SCOPE_RE = re.compile(r"所有|全部|全量|每个|全体|整个|每一", re.IGNORECASE)


def _parse_target(sql: str) -> tuple[str, str | None]:
    """从 UPDATE/DELETE 语句中提取 (表名, WHERE子句)；无 WHERE 时返回 None"""
    sql = sql.strip().rstrip(";")
    match = _UPDATE_RE.search(sql) or _DELETE_RE.search(sql)
    if not match:
        return "", None
    table = match.group(1).strip("`")
    where = _WHERE_RE.search(sql)
    return table, (where.group(1).strip() if where else None)


async def _count_table(runtime: Runtime[DataAgentContext], table: str) -> int:
    """查询表内总行数（命中率兜底校验用）"""
    try:
        rows = await runtime.context["dw_mysql_repository"].run(
            f"SELECT COUNT(*) AS cnt FROM `{table}`"
        )
        return rows[0]["cnt"] if rows else 0
    except Exception as exc:  # noqa: BLE001 计数失败不阻断，跳过兜底拦截
        logger.warning(f"全表行数统计失败，跳过命中率兜底：{exc}")
        return 0


async def _block_write(
    writer, state: DataAgentState, sql_type: str, sql: str, reason: str
) -> dict:
    """输出拦截提示并返回写回状态（不抓快照、不进审批）"""
    logger.warning(f"写操作安全拦截：{reason} | {sql}")
    writer({"type": "progress", "step": "安全校验", "status": "success"})
    writer({"type": "progress", "step": f"已拦截：{reason}", "status": "error"})
    return {"impact_summary": f"已拦截（{reason}）", "before_data": [], "blocked_reason": reason}


async def estimate_impact(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """预估写操作影响范围并写入状态，同时抓取执行前快照 before_data 供回滚使用

    - INSERT：固定新增 1 行，before_data 为空（回滚时按主键删除新插入的行）
    - UPDATE/DELETE：先做安全校验（无 WHERE / 恒真 WHERE / 命中全表 → 拦截），
      通过后通过 SELECT * WHERE 条件抓取命中行，行数即影响行数，
      命中行全量数据作为 before_data 快照（回滚时据此还原旧值/恢复被删行）
    """

    writer = runtime.stream_writer
    step = "预估影响范围"
    writer({"type": "progress", "step": step, "status": "running"})

    sql_type = state.get("sql_type", "select")
    sql = state.get("sql") or ""
    before_data: list[dict] = []
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
                verb = "更新" if sql_type == "update" else "删除"

                # ---- 安全校验 1：WHERE 缺失或恒真条件（1=1 等绕过手法） ----
                if where is None or _TRIVIAL_WHERE_RE.match(where or ""):
                    detail = "WHERE 恒真条件（如 1=1）" if where else "WHERE 缺失"
                    return await _block_write(
                        writer, state, sql_type, sql,
                        f"写操作{detail}，疑似全表{verb}，已取消执行；请补充明确条件"
                        "（如按 player_id / 日期 / 状态范围）后重试",
                    )

                # 影响预估 + 执行前快照（回滚依据）
                before_sql = f"SELECT * FROM `{table}` WHERE {where}"
                rows = await runtime.context["dw_mysql_repository"].run(before_sql)
                before_data = rows
                cnt = len(rows)

                # ---- 安全校验 2（兜底）：WHERE 命中行数 == 表内全量行数 ----
                total_rows = await _count_table(runtime, table)
                if total_rows and cnt == total_rows:
                    return await _block_write(
                        writer, state, sql_type, sql,
                        f"WHERE 条件命中表内全部 {cnt} 行，疑似全表{verb}，已取消执行；"
                        "请补充明确条件后重试",
                    )

                # ---- 安全校验 3（全量意图 + IN 部分枚举）：用户要"所有/全部"，
                #      但 WHERE 用 IN 只列出一部分值（如只写前 10 个 player_id）——
                #      会造成静默的部分执行，命中占比≥40% 即视为清单可能不完整 ----
                if (
                    total_rows
                    and cnt >= 5
                    and _FULL_SCOPE_RE.search(state.get("query") or "")
                    and " IN " in f" {sql.upper().replace(chr(10), ' ')} "
                ):
                    ratio = cnt / total_rows
                    if 0.4 <= ratio < 1.0:
                        return await _block_write(
                            writer, state, sql_type, sql,
                            f"用户意图包含全量{verb}，但 WHERE 用 IN 列举仅命中 {cnt}/{total_rows} 行"
                            f"（{ratio:.0%}），列表可能不完整会造成只{verb}部分数据，已取消执行；"
                            "请改用能覆盖全集的范围内条件（如日期/等级区间）或补全完整清单后重试",
                        )

                impact_summary = f"将{verb} {cnt} 行"
        logger.info(f"影响预估：{impact_summary}（快照 {len(before_data)} 行）")
        writer({"type": "progress", "step": step, "status": "success"})
        return {"impact_summary": impact_summary, "before_data": before_data}

    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        # 预估失败不阻断审批流程，提示人工核对
        return {"impact_summary": "影响范围预估失败，请人工核对", "before_data": []}
