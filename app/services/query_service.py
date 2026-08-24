"""
问数查询服务

负责把 API 层传入的自然语言问题转换成一次 LangGraph 工作流执行：
创建初始 State、组装 Runtime Context、消费 graph.astream 的流式输出，
并统一包装成 SSE 文本返回给路由层。

如果注入了会话历史仓储，还会把本次问数记录（用户问题 + 智能体回复）
在流结束时自动落库，实现前端"新会话"不丢失旧会话。
"""

import json
import time
import uuid
from datetime import date, datetime
from decimal import Decimal

from langchain_huggingface import HuggingFaceEndpointEmbeddings

from app.agent.context import DataAgentContext
from app.agent.graph import graph
from app.agent.state import DataAgentState
from app.entities.chat_message import ChatMessage
from app.entities.chat_session import ChatSession
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta.chat_session_repository import ChatSessionRepository
from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository


def _now_ms() -> int:
    """当前时间戳（epoch 毫秒）"""
    return int(time.time() * 1000)


def _make_id() -> str:
    """生成消息/会话 id（UUID 无横线）"""
    return uuid.uuid4().hex


def _json_safe(value):
    """把 Decimal / datetime 等非 JSON 类型转成可落 JSON 列的值"""
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


class QueryService:
    """封装一次问数查询所需的业务编排逻辑"""

    def __init__(
        self,
        meta_mysql_repository: MetaMySQLRepository,
        embedding_client: HuggingFaceEndpointEmbeddings,
        dw_mysql_repository: DWMySQLRepository,
        column_qdrant_repository: ColumnQdrantRepository,
        metric_qdrant_repository: MetricQdrantRepository,
        value_es_repository: ValueESRepository,
        chat_session_repository: ChatSessionRepository | None = None,
    ):
        # MySQL 仓储分别负责元数据补全和真实数仓环境信息读取
        self.meta_mysql_repository = meta_mysql_repository
        self.dw_mysql_repository = dw_mysql_repository

        # 召回链路依赖的向量检索、Embedding 和全文检索能力由依赖层注入
        self.embedding_client = embedding_client
        self.column_qdrant_repository = column_qdrant_repository
        self.metric_qdrant_repository = metric_qdrant_repository
        self.value_es_repository = value_es_repository

        # 会话历史仓储可选：注入后才执行落库
        self.chat_session_repository = chat_session_repository

    async def query(self, query: str, session_id: str | None = None):
        """执行一次问数工作流，并逐段产出 SSE 消息；流结束时按需落库"""

        repository = self.chat_session_repository
        created_session_id: str | None = None

        # ---- 流开始前：确定/创建会话，并保存用户消息 ----
        if repository is not None:
            if not session_id:
                # 前端未先建会话时的兜底：自动创建一个空会话
                now = _now_ms()
                chat_session = ChatSession(
                    id=_make_id(), title=None, created_at=now, updated_at=now
                )
                await repository.create_session(chat_session)
                session_id = chat_session.id
                created_session_id = session_id

            await repository.append_message(
                ChatMessage(
                    id=_make_id(),
                    session_id=session_id,
                    role="user",
                    content=query,
                    created_at=_now_ms(),
                )
            )
            # 会话标题以首条用户消息截断 30 字回填
            await repository.update_session_meta(session_id, query[:30], _now_ms())

        # 汇总本次智能体回复：执行步骤 / 摘要 / SQL / 结果样例 / 错误
        assistant: dict = {
            "steps": [],
            "content": None,
            "sql": None,
            "result_summary": None,
            "error": None,
        }
        saved = False

        # State 只放会被图节点读写和合并的业务数据，外部工具对象不塞进 State
        state = DataAgentState(query=query)
        # Context 保存本次图执行需要复用的外部依赖，节点通过 runtime.context 读取
        context = DataAgentContext(
            column_qdrant_repository=self.column_qdrant_repository,
            embedding_client=self.embedding_client,
            metric_qdrant_repository=self.metric_qdrant_repository,
            value_es_repository=self.value_es_repository,
            meta_mysql_repository=self.meta_mysql_repository,
            dw_mysql_repository=self.dw_mysql_repository,
        )
        try:
            # stream_mode="custom" 对应节点内部 writer(...) 写出的进度消息
            async for chunk in graph.astream(
                input=state, context=context, stream_mode="custom"
            ):
                # SSE 要求每条消息以 data: 开头，并以两个换行符结束
                # ensure_ascii=False 保留中文进度文案，default=str 兜底处理日期等非 JSON 类型
                yield f"data: {json.dumps(chunk, ensure_ascii=False, default=str)}\n\n"
                self._collect(chunk, assistant)

            # 正常结束：保存 assistant 回复
            if repository is not None:
                await self._save_assistant_message(repository, session_id, assistant)
                saved = True

        except Exception as e:
            # 流式接口已经开始返回后不能再改 HTTP 状态码，因此把异常也包装成一条 SSE 消息
            error = {"type": "error", "message": str(e)}
            yield f"data: {json.dumps(error, ensure_ascii=False, default=str)}\n\n"
            # 异常时也把失败的回复记入历史，方便之后回看
            if repository is not None:
                assistant["error"] = str(e)
                assistant["content"] = "这次查询没有成功。"
                await self._save_assistant_message(repository, session_id, assistant)
                saved = True

        finally:
            if repository is not None and not saved:
                # 客户端取消等场景：不写半截回复，只刷新会话更新时间
                await repository.update_session_meta(session_id, None, _now_ms())
            if created_session_id:
                # 兜底创建会话的 id 通过 SSE 尾事件回传前端
                try:
                    event = {"type": "session_created", "session_id": created_session_id}
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except Exception:  # noqa: BLE001 取消时 generator 已关闭，忽略
                    pass

    # ------------------------------------------------------------------ 落库辅助

    @staticmethod
    def _collect(chunk: dict, assistant: dict):
        """从节点输出中摘取落库所需的字段"""
        ctype = chunk.get("type")
        if ctype == "progress":
            step = chunk.get("step")
            status = chunk.get("status")
            if step and status in ("running", "success", "error"):
                assistant["steps"].append({"step": step, "status": status})
        elif ctype == "result":
            data = chunk.get("data") or []
            assistant["sql"] = chunk.get("sql")
            if isinstance(data, list):
                assistant["content"] = f"查询完成，共 {len(data)} 行结果。"
                assistant["result_summary"] = _json_safe(data[:10])
            else:
                assistant["content"] = "查询完成，已返回结构化结果。"
                assistant["result_summary"] = _json_safe(data)
        elif ctype == "error":
            assistant["error"] = chunk.get("message", "")
            assistant["content"] = "这次查询没有成功。"

    async def _save_assistant_message(
        self, repository: ChatSessionRepository, session_id: str, assistant: dict
    ):
        """保存一条 assistant 消息并刷新会话更新时间"""
        content = assistant["content"] or "流程已结束，后端未返回查询结果。"
        await repository.append_message(
            ChatMessage(
                id=_make_id(),
                session_id=session_id,
                role="assistant",
                content=content,
                steps=assistant["steps"] or None,
                sql=assistant["sql"],
                result_summary=assistant["result_summary"],
                error=assistant["error"],
                created_at=_now_ms(),
            )
        )
        await repository.update_session_meta(session_id, None, _now_ms())
