"""
人工审批等待节点

负责在写操作执行前用 interrupt() 暂停 LangGraph 流程，等待用户在审核
卡片上确认或取消。暂停期间图状态由 checkpointer 持久化，用户通过
/api/human-feedback 恢复执行：确认(resume=True) 走执行，取消(resume=False)
回到生成SQL节点重新生成。
"""

from langgraph.runtime import Runtime
from langgraph.types import interrupt

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger


def wait_for_human_input(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """暂停流程等待人工审批，返回 human_action"""

    writer = runtime.stream_writer
    step = "等待人工确认"
    writer({"type": "progress", "step": step, "status": "running"})

    # 先把审批信息通过 SSE 发给前端，前端据此渲染审核卡片
    writer(
        {
            "type": "human_approval",
            "sql": state.get("sql", ""),
            "sql_type": state.get("sql_type", ""),
            "impact_summary": state.get("impact_summary", ""),
        }
    )

    # 暂停：返回值来自 Command(resume=...)，True/approve=确认，False/reject=取消
    decision = interrupt(
        {
            "sql": state.get("sql", ""),
            "sql_type": state.get("sql_type", ""),
            "impact_summary": state.get("impact_summary", ""),
        }
    )
    action = "approve" if decision is True or decision == "approve" else "reject"
    logger.info(f"人工审批结果：{action}")
    writer({"type": "progress", "step": step, "status": "success"})
    return {"human_action": action}
