"""
Time-Travel 回滚接口请求响应结构
"""

from pydantic import BaseModel


class AuditLogOut(BaseModel):
    """写操作审计记录（前端回滚列表数据源）"""

    log_id: int
    session_id: str | None
    seq: int
    op_type: str
    table_name: str
    sql_text: str
    before_data: list | None
    row_count: int
    status: str
    created_at: str | None


class RollbackRequest(BaseModel):
    """回滚请求体：目标审计记录 id"""

    log_id: int


class RollbackResponse(BaseModel):
    """回滚结果摘要"""

    log_id: int
    rolled_back: int  # 连带回滚的操作数（含目标）
    restored_rows: int  # 恢复/还原的总行数
    descriptions: list[str]  # 每一步还原的动作描述
