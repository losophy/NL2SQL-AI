"""
会话消息业务实体

用于表达会话内的一条消息：用户问题或智能体回复
智能体回复会携带执行步骤、SQL 与结果摘要，统一复用这份业务对象
"""

from dataclasses import dataclass, field


@dataclass
class ChatMessage:
    """系统内部统一使用的会话消息表达"""

    id: str
    session_id: str
    role: str
    content: str
    created_at: int
    # 以下字段仅智能体回复会携带
    steps: list | None = field(default=None)
    sql: str | None = field(default=None)
    result_summary: list | None = field(default=None)
    error: str | None = field(default=None)
    # 写操作执行成功后的审计记录 id：非空时前端在消息左侧展示回滚入口
    audit_log_id: int | None = field(default=None)
