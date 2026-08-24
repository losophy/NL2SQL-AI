"""
会话历史接口请求与响应结构

集中声明会话列表 创建 详情与删除接口的出入参结构
"""

from pydantic import BaseModel


class SessionListItem(BaseModel):
    """会话列表项：只包含侧边栏展示所需字段"""

    id: str
    title: str | None
    created_at: int
    updated_at: int


class SessionCreateResp(SessionListItem):
    """创建会话的响应"""

    pass


class MessageOut(BaseModel):
    """会话详情里的单条消息"""

    id: str
    role: str
    content: str
    steps: list | None = None
    sql: str | None = None
    result_summary: list | None = None
    error: str | None = None
    audit_log_id: int | None = None
    created_at: int


class SessionDetailResp(SessionListItem):
    """会话详情：元信息 + 全部消息"""

    messages: list[MessageOut]
