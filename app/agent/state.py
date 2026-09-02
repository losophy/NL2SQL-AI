"""
电商问数 Agent 状态定义

State 是 LangGraph 各节点之间传递和更新的共享数据
本章在用户原始问题之外，新增关键词列表和三路召回结果
并把召回到的实体整理成后续提示词更容易消费的表信息和指标信息
SQL 生成闭环会继续写入候选 SQL 以及校验错误信息，用于控制校正或执行分支
"""

from typing import TypedDict

from app.entities.column_info import ColumnInfo
from app.entities.metric_info import MetricInfo
from app.entities.value_info import ValueInfo


class MetricInfoState(TypedDict):
    """面向 SQL 生成提示词的指标信息"""

    name: str
    description: str
    # 指标依赖的字段 id，用来提示模型不要脱离业务口径随意计算
    relevant_columns: list[str]
    alias: list[str]


class ColumnInfoState(TypedDict):
    """表上下文中的字段信息"""

    name: str
    type: str
    role: str
    # 字段真实样例值，尤其用于辅助 where 条件里的枚举值选择
    examples: list
    description: str
    alias: list[str]


class TableInfoState(TypedDict):
    """SQL 生成阶段真正传给模型的表结构上下文"""

    name: str
    role: str
    description: str
    columns: list[ColumnInfoState]


class DateInfoState(TypedDict):
    """SQL 生成阶段使用的当前日期上下文"""

    date: str
    weekday: str
    quarter: str


class DBInfoState(TypedDict):
    """SQL 生成阶段使用的数据库环境信息"""

    dialect: str
    version: str


class DataAgentState(TypedDict):
    """一次问数链路中的核心状态"""

    query: str  # 用户输入的查询
    keywords: list[str]  # 抽取的关键词
    retrieved_column_infos: list[ColumnInfo]  # 检索到的字段信息
    retrieved_metric_infos: list[MetricInfo]  # 检索到的指标信息
    retrieved_value_infos: list[ValueInfo]  # 检索到的取值信息

    table_infos: list[TableInfoState]  # 合并和补齐后的表结构上下文
    metric_infos: list[MetricInfoState]  # 合并后的指标上下文
    date_info: DateInfoState  # 当前日期 星期和季度信息
    db_info: DBInfoState  # 数据库方言和版本信息

    sql: str  # 生成或校正后的SQL

    error: str  # 校验SQL时出现的错误信息

    # ---- HITL 写操作审批相关 ----
    sql_type: str  # SQL 操作类型：select / insert / update / delete
    impact_summary: str  # 写操作影响范围预估描述（如"新增 1 行"、"将修改 3 行"）
    pk_note: str  # INSERT 主键预检说明（如"主键 C011 已被占用，已自动分配 C021"）
    human_action: str  # 人工审批结果：approve / reject
    conversation_id: str  # 线程 id，用于 interrupt 暂停与恢复
    blocked_reason: str  # 写操作安全拦截原因（estimate_impact 检测到疑似全表操作时写入；非空则跳过人工审批直接取消）

    # ---- Time-Travel 回滚相关 ----
    session_id: str | None  # 所属会话 id（由 query_service 入口写入，用于审计日志归属）
    before_data: list[dict]  # 写操作执行前的受影响行快照（estimate_impact 抓取，回滚依据）
    audit_log_id: int | None  # 本次写操作落库后的审计记录 id（run_sql 写入）
