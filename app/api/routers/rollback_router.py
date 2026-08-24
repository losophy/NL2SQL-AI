"""
Time-Travel 回滚接口路由

提供写操作审计日志查询与回滚接口：
- GET /api/audit-logs：按会话返回写操作历史（前端回滚列表）
- POST /api/rollback：回滚目标操作及其之后的所有操作（LIFO 逆序）
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_rollback_service
from app.api.schemas.rollback_schema import (
    AuditLogOut,
    RollbackRequest,
    RollbackResponse,
)
from app.services.rollback_service import RollbackService

rollback_router = APIRouter()


@rollback_router.get("/api/audit-logs", response_model=list[AuditLogOut])
async def list_audit_logs(
    session_id: str,
    rollback_service: Annotated[RollbackService, Depends(get_rollback_service)],
):
    """返回某会话的全部写操作审计记录（按执行顺序正序）"""
    return await rollback_service.list_audit_logs(session_id)


@rollback_router.post("/api/rollback", response_model=RollbackResponse)
async def rollback(
    req: RollbackRequest,
    rollback_service: Annotated[RollbackService, Depends(get_rollback_service)],
):
    """回滚目标操作及其之后的所有操作，返回回滚摘要"""
    try:
        return await rollback_service.rollback(req.log_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
