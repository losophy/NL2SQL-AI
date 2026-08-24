"""
会话历史接口路由

提供会话的创建 列表 详情与删除接口
路由层只处理请求体 依赖声明和响应结构，业务编排收敛在 SessionService
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response

from app.api.dependencies import get_session_service
from app.api.schemas.session_schema import (
    MessageOut,
    SessionCreateResp,
    SessionDetailResp,
    SessionListItem,
)
from app.services.session_service import SessionService

session_router = APIRouter()


@session_router.get("/api/sessions", response_model=list[SessionListItem])
async def list_sessions(
    session_service: Annotated[SessionService, Depends(get_session_service)],
):
    """返回全部历史会话（按更新时间倒序）"""
    sessions = await session_service.list_sessions()
    return [
        SessionListItem(
            id=s.id, title=s.title, created_at=s.created_at, updated_at=s.updated_at
        )
        for s in sessions
    ]


@session_router.post("/api/sessions", response_model=SessionCreateResp)
async def create_session(
    session_service: Annotated[SessionService, Depends(get_session_service)],
):
    """创建一个空会话"""
    chat_session = await session_service.create_session()
    return SessionCreateResp(
        id=chat_session.id,
        title=chat_session.title,
        created_at=chat_session.created_at,
        updated_at=chat_session.updated_at,
    )


@session_router.get("/api/sessions/{session_id}", response_model=SessionDetailResp)
async def get_session(
    session_id: str,
    session_service: Annotated[SessionService, Depends(get_session_service)],
):
    """返回会话详情：元信息 + 按时间正序的消息列表"""
    detail = await session_service.get_session_detail(session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    chat_session, messages = detail
    return SessionDetailResp(
        id=chat_session.id,
        title=chat_session.title,
        created_at=chat_session.created_at,
        updated_at=chat_session.updated_at,
        messages=[
            MessageOut(
                id=m.id,
                role=m.role,
                content=m.content,
                steps=m.steps,
                sql=m.sql,
                result_summary=m.result_summary,
                error=m.error,
                created_at=m.created_at,
            )
            for m in messages
        ],
    )


@session_router.delete("/api/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    session_service: Annotated[SessionService, Depends(get_session_service)],
):
    """删除会话及其全部消息"""
    deleted = await session_service.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="会话不存在")
    return Response(status_code=204)
