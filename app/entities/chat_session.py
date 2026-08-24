"""
会话历史业务实体

用于表达一次前端会话：会话 id、标题、创建与更新时间
后续写入 Meta MySQL 时复用这份统一的业务对象
"""

from dataclasses import dataclass


@dataclass
class ChatSession:
    """系统内部统一使用的会话历史表达"""

    id: str
    title: str | None
    created_at: int
    updated_at: int
