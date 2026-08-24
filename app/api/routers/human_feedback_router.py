"""
人工审批接口路由

提供写操作审批的续流接口：用户在前端审核卡片确认或取消后，
用 LangGraph 的 Command(resume=...) 从断点恢复执行，并把后续 SSE 事件返回前端。
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from starlette.responses import StreamingResponse

from app.api.dependencies import get_query_service
from app.api.schemas.human_feedback_schema import HumanFeedbackSchema
from app.services.query_service import QueryService

human_feedback_router = APIRouter()


@human_feedback_router.post("/api/human-feedback")
async def human_feedback(
    feedback: HumanFeedbackSchema,
    query_service: Annotated[QueryService, Depends(get_query_service)],
):
    """接收用户在审核卡片上的决策，恢复被中断的写操作审批流程"""

    return StreamingResponse(
        query_service.resume(
            thread_id=feedback.thread_id,
            action=feedback.action,
            session_id=feedback.session_id,
        ),
        media_type="text/event-stream",
    )
