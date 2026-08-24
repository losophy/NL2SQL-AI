"""
人工审批接口请求结构

定义写操作审批所需的入参：线程 id、审批动作和可选会话 id
"""

from typing import Literal

from pydantic import BaseModel


class HumanFeedbackSchema(BaseModel):
    """`/api/human-feedback` 请求体：用户在审核卡片上的确认/取消"""

    thread_id: str  # 审批暂停时的线程 id（来自 human_approval 事件）
    action: Literal["approve", "reject"]  # 确认执行 / 取消并重新生成
    session_id: str | None = None  # 可选：续流结束后会话落库归属
