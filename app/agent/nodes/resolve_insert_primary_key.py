"""
INSERT 主键预检与自动分配节点

问题背景：generate_sql 提示词要求 INSERT 必须携带主键，但 LLM 只能看到
meta 中抽取的少量样例值（通常只有前 10 个），容易编造出与表中已有记录
冲突的主键（例如 dim_player 已有 C011 却仍生成 C011），执行时触发
MySQL 1062 Duplicate entry 报错；多行 VALUES 时还可能某行漏写主键，
导致 1136 Column count doesn't match。

本节点在写操作进入人工审批前介入，仅对 INSERT 生效，支持多行 VALUES：
- 解析 INSERT 的目标表、列清单、多行值清单；
- 查询该表主键列及现有主键值集合；
- 主键列缺失：自动补列，并为每行分配不冲突的新主键；
- 某行值数量不足：缺失主键补新值、缺失其他列补 NULL，保证列数与值数一致；
- 主键值已被占用：自动分配不冲突的新主键替换；
- 替换结果以 pk_note 写入 state，随影响预估一并展示在审批卡片上。
"""

import re
import time

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger

# 匹配 INSERT/REPLACE 的表名、列清单与 VALUES 段（VALUES 之后整段交给行级正则拆分）
_INSERT_RE = re.compile(
    r"\b(INSERT\s+(?:IGNORE\s+)?INTO|REPLACE\s+INTO)\s+([`\w]+)\s*(?:\(([^)]*)\))?\s*VALUES\s*(.+)",
    re.IGNORECASE | re.DOTALL,
)
# 从 VALUES 段中提取每一行 (…) 的值内容
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


def _quote_like(old_raw: str, new_val: str) -> str:
    """沿用原值的引号风格包裹新值；无引号时按是否为纯数字决定"""
    old = old_raw.strip()
    if old.startswith('"'):
        return f'"{new_val}"'
    if old.startswith("'"):
        return f"'{new_val}'"
    return new_val if new_val.isdigit() else f"'{new_val}'"


def _next_pk(existing: set[str], raw_val: str) -> str:
    """返回一个不冲突的主键

    - 原值本身未被占用：直接复用（避免误替换）；
    - 原值已被占用：按"前缀+数字"递增 +1，保留前缀与位数；
    - 无原值（缺主键）：从现有主键推断"前缀+数字"系列的最大值 +1；
    - 兜底：原值/占位追加时间戳后缀。
    """
    base = _unquote(raw_val).strip()
    if base and base not in existing:
        return base

    # 有原值且形如 前缀+数字：数字 +1
    if base:
        m = re.search(r"^(.*?)(\d+)$", base)
        if m:
            prefix, num_str = m.group(1), m.group(2)
            width = len(num_str)
            n = int(num_str)
            for _ in range(100000):
                n += 1
                cand = f"{prefix}{str(n).zfill(width)}"
                if cand not in existing:
                    return cand

    # 从现有主键推断"前缀+数字"系列（用于缺主键时自动分配）
    series: dict[str, list[tuple[str, int]]] = {}
    for v in existing:
        mm = re.match(r"^(.*?)(\d+)$", str(v))
        if mm:
            series.setdefault(mm.group(1), []).append((mm.group(2), int(mm.group(2))))
    if series:
        # 取样本最多的前缀，视为该表的主键命名系列
        prefix = max(series, key=lambda p: len(series[p]))
        pairs = series[prefix]
        width = max(len(num_str) for num_str, _ in pairs)
        n = max(num for _, num in pairs)
        for _ in range(100000):
            n += 1
            cand = f"{prefix}{str(n).zfill(width)}"
            if cand not in existing:
                return cand

    # 兜底：原值/占位 + 时间戳后缀
    fallback_base = base or "PK"
    for i in range(1000):
        cand = f"{fallback_base}_{time.strftime('%y%m%d%H%M%S')}{i}"
        if cand not in existing:
            return cand
    return f"{fallback_base}_{int(time.time() * 1000)}"


def _rebuild_insert(m: re.Match, cols: list[str], rows: list[list[str]]) -> str:
    """按解析结果重新拼接 INSERT 语句（支持多行 VALUES），其余部分原样保留"""
    head = m.group(1)  # INSERT INTO / INSERT IGNORE INTO / REPLACE INTO
    table = m.group(2).strip("`")
    values_part = ", ".join(f"({', '.join(r)})" for r in rows)
    new_stmt = f"{head} `{table}` ({', '.join(cols)}) VALUES {values_part}"
    return m.string[: m.start()] + new_stmt + m.string[m.end():]


async def resolve_insert_primary_key(
    state: DataAgentState, runtime: Runtime[DataAgentContext]
):
    """INSERT 主键预检：多行逐行校验，冲突自动分配新主键，缺失自动补列/补值"""

    writer = runtime.stream_writer
    step = "主键预检"
    writer({"type": "progress", "step": step, "status": "running"})

    sql = state.get("sql") or ""
    if state.get("sql_type") != "insert":
        # 非 INSERT 写操作不涉及主键分配，直接放行
        return {}

    note = ""
    try:
        match = _INSERT_RE.search(sql)
        if not match:
            logger.info("主键预检：未能解析 INSERT 语句，跳过自动分配")
            return {}

        table = match.group(2).strip("`")
        dw = runtime.context["dw_mysql_repository"]
        cols_raw = match.group(3)
        cols = [c.strip().strip("`") for c in _split_commas(cols_raw)] if cols_raw else []
        rows = [_split_commas(rg) for rg in _VALUES_ROW_RE.findall(match.group(4))]
        if not cols or not rows:
            return {}

        pk_cols = await dw.get_primary_keys(table)
        if not pk_cols:
            # 表没有主键（教学数仓中少见），无需干预
            return {}

        pk = pk_cols[0]
        existing = set(await dw.get_column_values(table, pk, limit=100000))
        notes: list[str] = []
        changed = False
        # 本次由本节点新分配的主键值：后续冲突检测应跳过（它们一定不冲突）
        fresh_values: set[str] = set()

        def _alloc_pk() -> str:
            """分配一个新主键并登记到 existing，避免同批多行分到同一个值"""
            value = _next_pk(existing, "")
            existing.add(value)
            fresh_values.add(value)
            return value

        # 1) 主键列缺失：补列，并为每行补一个主键值
        if pk not in cols:
            cols.append(pk)
            for row in rows:
                row.append(_quote_like("", _alloc_pk()))
            changed = True
            notes.append(
                f"INSERT 缺少主键字段 {pk}，已为 {len(rows)} 行自动分配主键"
                f"（{pk}={', '.join(_unquote(row[-1]) for row in rows)}）"
            )

        # 2) 逐行规整列数并处理主键冲突
        idx = cols.index(pk)
        for i, row in enumerate(rows, start=1):
            if len(row) < len(cols):
                # 值数量不足：缺失列按位置补齐（主键补新值，其他列补 NULL）
                for col in cols[len(row):]:
                    if col == pk:
                        row.append(_quote_like("", _alloc_pk()))
                        changed = True
                    else:
                        row.append("NULL")
            elif len(row) > len(cols):
                # 值数量超出：按列数截断
                row[:] = row[: len(cols)]
                changed = True

            # 主键冲突检测与替换（本节点新分配的值跳过）
            old_val = row[idx].strip()
            if _unquote(old_val) not in fresh_values:
                new_val = _next_pk(existing, old_val)
                if new_val != _unquote(old_val):
                    row[idx] = _quote_like(old_val, new_val)
                    notes.append(
                        f"第{i}行主键 {pk}={_unquote(old_val)} 已被占用，已自动分配新主键 {pk}={new_val}"
                    )
                    changed = True
            # 登记该行最终主键，防止同批后续行分配到重复值
            existing.add(_unquote(row[idx]))

        if changed:
            sql = _rebuild_insert(match, cols, rows)
            note = "；".join(notes) if notes else "已自动调整主键"
            logger.info(f"主键预检：{note}")
            return {"sql": sql, "pk_note": note}
    except Exception as e:  # noqa: BLE001 预检失败不阻断流程，交由后续执行与兜底处理
        logger.error(f"{step} failed: {e}")

    writer({"type": "progress", "step": step, "status": "success"})
    return {}
