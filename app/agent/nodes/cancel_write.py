"""
写操作取消节点

用户点击审批卡片的"取消不执行"后进入本节点：不执行 SQL、也不重新生成，
直接结束本次写操作流程。前端会在审批后收回对应的确认卡片消息。
"""

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger


def cancel_write(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """结束写操作流程，不执行不重新生成"""

    writer = runtime.stream_writer
    step = "写操作已取消"
    writer({"type": "progress", "step": step, "status": "success"})
    logger.info("写操作已取消，未执行")
    return {}
