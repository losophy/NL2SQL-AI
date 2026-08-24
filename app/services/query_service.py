"""
问数查询服务

负责把 API 层传入的自然语言问题转换成一次 LangGraph 工作流执行：
创建初始 State、组装 Runtime Context、消费 graph.astream 的流式输出，
并统一包装成 SSE 文本返回给路由层。

如果注入了会话历史仓储，还会把本次问数记录（用户问题 + 智能体回复）
在流结束时自动落库，实现前端"新会话"不丢失旧会话。
"""

import json
import re
import time
import uuid
from datetime import date, datetime
from decimal import Decimal

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_huggingface import HuggingFaceEndpointEmbeddings
from langgraph.types import Command

from app.agent.context import DataAgentContext
from app.agent.graph import graph
from app.agent.llm import llm
from app.agent.state import DataAgentState
from app.core.log import logger
from app.entities.chat_message import ChatMessage
from app.entities.chat_session import ChatSession
from app.prompt.prompt_loader import load_prompt
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta.chat_session_repository import ChatSessionRepository
from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository
from app.repositories.mysql.meta.write_audit_log_repository import (
    WriteAuditLogRepository,
)
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


# 表元数据问题识别：命中则走快捷路径直接查库，不跑完整问数流程
TABLE_META_RE = re.compile(
    r"(多少张?表|几个表|所有表|全部表|哪些表|有哪些表|表(?:的)?(?:列表|清单|名)|列出.*表|(?:显示|查看|展示)(?:一下|下|出)?所有?表)"
)
# 具体表名识别（dim_/fact_ 等前缀）：命中说明用户指的是某张表（如"显示 dim_player 表"），
# 应走正常查询而非表清单快捷路径。用 ASCII 前后缀断言代替 \b（中文也是 \w，\b 会失效）
TABLE_NAME_RE = re.compile(
    r"(?<![a-z0-9_])(?:dim|fact|dws|ads)_[a-z0-9_]+(?![a-z0-9_])",
    re.IGNORECASE,
)
# "显示指定表"意图识别：如"显示下dim_player 表 / 查看 dim_player 表的数据"，
# 明确指定某张表且无附加查询条件，直接 SELECT * 该表返回，不跑完整 LLM 流程
TABLE_VIEW_RE = re.compile(
    r"(?:显示|查看|展示|看看|看下|列出|给我看)\s*(?:下|一下|出)?\s*"
    r"(?:dim|fact|dws|ads)_[a-z0-9_]+\s*表(?:的)?(?:数据|内容|信息|全部|所有|全量)?$",
    re.IGNORECASE,
)
# 表数量超过该阈值时不再返回全量数据，只提示用户去数据库查询
TABLE_LIST_THRESHOLD = 10


def extract_table_view_name(query: str) -> str | None:
    """从"显示 X 表"类问题中提取目标表名；非此类问题返回 None"""
    if not TABLE_VIEW_RE.search(query):
        return None
    match = TABLE_NAME_RE.search(query)
    return match.group(0).lower() if match else None

# 表结构 DDL 意图识别：命中则直接提示"不支持自动建表"，不跑完整问数流程
# 覆盖：新建/创建/增加/删除表、建一张 X 表、create/drop/alter table 等 Schema 级操作
TABLE_DDL_RE = re.compile(
    r"(create\s+table|drop\s+table|alter\s+table"
    r"|建.{0,12}表|新建.{0,12}表|创建.{0,12}表"
    r"|增加.{0,6}表|新增.{0,6}表|添加.{0,6}表"
    r"|删除.{0,12}表|删掉.{0,12}表|删表)",
    re.IGNORECASE,
)


def is_table_meta_question(query: str) -> bool:
    """判断用户问题是否为“数据库表清单/表数量”类元数据问题"""
    if not TABLE_META_RE.search(query):
        return False
    # 若用户明确提到了某张具体表（如"显示 dim_player 表"），意图是查看该表数据，
    # 不应走"列出所有表"的快捷路径，交给正常问数流程生成 SELECT
    if TABLE_NAME_RE.search(query):
        return False
    return True


def is_table_ddl_question(query: str) -> bool:
    """判断用户问题是否为"新建/删除表结构"（Schema 级 DDL）类问题"""
    return bool(TABLE_DDL_RE.search(query))


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
        write_audit_log_repository: WriteAuditLogRepository | None = None,
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
        # 写操作审计仓储：注入后写操作执行成功会落库审计日志（支撑 Time-Travel 回滚）
        self.write_audit_log_repository = write_audit_log_repository

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
            "audit_log_id": None,
        }
        saved = False

        # ---- 建表/删表（Schema 级 DDL）问题快捷路径：提示手动流程，不跑完整问数流程 ----
        if is_table_ddl_question(query):
            # 与完整问数流程一致的流式反馈：步骤逐个 running→success，DDL 生成期间保持"正在执行"提示
            steps_list: list[dict] = []

            def emit(step: str, status: str) -> dict:
                event = {"type": "progress", "step": step, "status": status}
                steps_list.append({"step": step, "status": status})
                return event

            yield f"data: {json.dumps(emit('识别建表/删表意图', 'running'), ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps(emit('识别建表/删表意图', 'success'), ensure_ascii=False)}\n\n"

            yield f"data: {json.dumps(emit('生成建表/删表SQL', 'running'), ensure_ascii=False)}\n\n"
            ddl_sql = ""
            try:
                ddl_sql = await self._generate_ddl_sql(query)
            except Exception as exc:  # noqa: BLE001 LLM 生成失败不影响提示流程
                logger.warning(f"生成建表/删表 SQL 失败：{exc}")
            yield f"data: {json.dumps(emit('生成建表/删表SQL', 'success'), ensure_ascii=False)}\n\n"

            yield f"data: {json.dumps(emit('返回手动操作提示', 'running'), ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps(emit('返回手动操作提示', 'success'), ensure_ascii=False)}\n\n"

            table_steps = [
                {"步骤": "1. 在 dw 数仓执行下方 SQL 完成建表/删表；建表后可用 INSERT 灌入示例数据"},
                {"步骤": "2. 建表后需同步元数据：编辑 conf/meta_config.yaml，在 tables: 下追加该表的列定义（列名/角色/别名/描述）"},
                {"步骤": "3. 重跑元数据构建：uv run python -m app.scripts.build_meta_knowledge -c conf/meta_config.yaml（重跑前先清空 meta 4 张表，避免主键冲突）"},
                {"步骤": "4. 验证：再次问数时能命中新表即同步成功"},
            ]
            assistant = {
                "steps": steps_list,
                "content": "当前不支持自动建表，请按以下步骤手动操作。",
                "sql": ddl_sql or None,
                "result_summary": [
                    {
                        "表名": "手动建表步骤（Agent 暂不支持自动建表）",
                        "行数": len(table_steps),
                        "数据": table_steps,
                    }
                ],
                "error": None,
            }
            result_event: dict = {"type": "result", "data": assistant["result_summary"] or []}
            if assistant["sql"]:
                result_event["sql"] = assistant["sql"]
            yield f"data: {json.dumps(result_event, ensure_ascii=False, default=str)}\n\n"

            if repository is not None:
                await self._save_assistant_message(repository, session_id, assistant)
            if created_session_id:
                # 兜底创建会话的 id 通过 SSE 尾事件回传前端
                try:
                    event = {"type": "session_created", "session_id": created_session_id}
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except Exception:  # noqa: BLE001 取消时 generator 已关闭，忽略
                    pass
            return

        # ---- 表元数据问题快捷路径：不走完整问数流程，直接查 dw 库 ----
        if is_table_meta_question(query):
            assistant = await self._handle_table_meta_query(query)
            result_event: dict = {"type": "result", "data": assistant["result_summary"] or []}
            if assistant["sql"]:
                result_event["sql"] = assistant["sql"]
            yield f"data: {json.dumps(result_event, ensure_ascii=False, default=str)}\n\n"

            if repository is not None:
                await self._save_assistant_message(repository, session_id, assistant)
            if created_session_id:
                # 兜底创建会话的 id 通过 SSE 尾事件回传前端
                try:
                    event = {"type": "session_created", "session_id": created_session_id}
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except Exception:  # noqa: BLE001 取消时 generator 已关闭，忽略
                    pass
            return

        # ---- "显示指定表"快捷路径：明确指定某张表且无附加条件，直接查该表全量数据 ----
        table_name = extract_table_view_name(query)
        if table_name:
            view_steps: list[dict] = []

            def vemit(step: str, status: str) -> dict:
                event = {"type": "progress", "step": step, "status": status}
                view_steps.append({"step": step, "status": status})
                return event

            yield f"data: {json.dumps(vemit('识别查看表意图', 'running'), ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps(vemit('识别查看表意图', 'success'), ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps(vemit('查询表数据', 'running'), ensure_ascii=False)}\n\n"

            rows: list[dict] = []
            try:
                rows = await self.dw_mysql_repository.fetch_table_data(table_name)
            except Exception as exc:  # noqa: BLE001 查表失败不阻断，返回空结果
                logger.warning(f"查询表 {table_name} 数据失败：{exc}")
            yield f"data: {json.dumps(vemit('查询表数据', 'success'), ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps(vemit('返回结果', 'running'), ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps(vemit('返回结果', 'success'), ensure_ascii=False)}\n\n"

            select_sql = f"SELECT * FROM `{table_name}`"
            assistant = {
                "steps": view_steps,
                "content": f"已返回 {table_name} 表的全部数据。",
                "sql": select_sql,
                "result_summary": [
                    {"表名": table_name, "行数": len(rows), "数据": _json_safe(rows)}
                ],
                "error": None,
            }
            result_event: dict = {
                "type": "result",
                "data": assistant["result_summary"],
                "sql": select_sql,
            }
            yield f"data: {json.dumps(result_event, ensure_ascii=False, default=str)}\n\n"

            if repository is not None:
                await self._save_assistant_message(repository, session_id, assistant)
            if created_session_id:
                # 兜底创建会话的 id 通过 SSE 尾事件回传前端
                try:
                    event = {"type": "session_created", "session_id": created_session_id}
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except Exception:  # noqa: BLE001 取消时 generator 已关闭，忽略
                    pass
            return

        # State 只放会被图节点读写和合并的业务数据，外部工具对象不塞进 State
        state = DataAgentState(query=query, session_id=session_id)
        # Context 保存本次图执行需要复用的外部依赖，节点通过 runtime.context 读取
        context = DataAgentContext(
            column_qdrant_repository=self.column_qdrant_repository,
            embedding_client=self.embedding_client,
            metric_qdrant_repository=self.metric_qdrant_repository,
            value_es_repository=self.value_es_repository,
            meta_mysql_repository=self.meta_mysql_repository,
            dw_mysql_repository=self.dw_mysql_repository,
            write_audit_log_repository=self.write_audit_log_repository,
        )
        # 每次问数一个独立线程 id，用于写操作审批的暂停与恢复
        thread_id = _make_id()
        config = {"configurable": {"thread_id": thread_id}}
        interrupted = False
        try:
            # stream_mode="custom" 对应节点内部 writer(...) 写出的进度消息
            async for chunk in graph.astream(
                input=state,
                context=context,
                stream_mode="custom",
                config=config,
            ):
                # 写操作触发人工审批：interrupt 后 astream 正常结束，流程挂起等待前端确认
                if isinstance(chunk, dict) and "__interrupt__" in chunk:
                    interrupted = True
                    break

                # 审批信息事件附加 thread_id，前端据此发起 /api/human-feedback 续流
                if isinstance(chunk, dict) and chunk.get("type") == "human_approval":
                    chunk = {**chunk, "thread_id": thread_id}

                # SSE 要求每条消息以 data: 开头，并以两个换行符结束
                # ensure_ascii=False 保留中文进度文案，default=str 兜底处理日期等非 JSON 类型
                yield f"data: {json.dumps(chunk, ensure_ascii=False, default=str)}\n\n"
                self._collect(chunk, assistant)

            # 正常结束（未被审批中断）：保存 assistant 回复
            if not interrupted and repository is not None:
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

    # ------------------------------------------------------------------ 审批续流

    async def resume(
        self, thread_id: str, action: str, session_id: str | None = None
    ):
        """用户在审核卡片确认/取消后，从断点恢复执行并产出后续 SSE 事件

        - approve：恢复后继续执行 SQL（run_sql）
        - reject：恢复后回到生成 SQL 重新生成，若仍是写操作会再次中断等待审批
        """
        repository = self.chat_session_repository
        assistant: dict = {
            "steps": [],
            "content": None,
            "sql": None,
            "result_summary": None,
            "error": None,
            "audit_log_id": None,
        }
        context = DataAgentContext(
            column_qdrant_repository=self.column_qdrant_repository,
            embedding_client=self.embedding_client,
            metric_qdrant_repository=self.metric_qdrant_repository,
            value_es_repository=self.value_es_repository,
            meta_mysql_repository=self.meta_mysql_repository,
            dw_mysql_repository=self.dw_mysql_repository,
            write_audit_log_repository=self.write_audit_log_repository,
        )
        config = {"configurable": {"thread_id": thread_id}}
        resume_value = True if action == "approve" else False
        interrupted = False
        saved = False
        try:
            async for chunk in graph.astream(
                Command(resume=resume_value),
                context=context,
                stream_mode="custom",
                config=config,
            ):
                if isinstance(chunk, dict) and "__interrupt__" in chunk:
                    # 重新生成的 SQL 仍是写操作：再次暂停等待新一轮审批
                    interrupted = True
                    break
                if isinstance(chunk, dict) and chunk.get("type") == "human_approval":
                    chunk = {**chunk, "thread_id": thread_id}
                yield f"data: {json.dumps(chunk, ensure_ascii=False, default=str)}\n\n"
                self._collect(chunk, assistant)

            if not interrupted and repository is not None and session_id:
                await self._save_assistant_message(repository, session_id, assistant)
                saved = True

        except Exception as e:
            error = {"type": "error", "message": str(e)}
            yield f"data: {json.dumps(error, ensure_ascii=False, default=str)}\n\n"
            if repository is not None and session_id:
                assistant["error"] = str(e)
                assistant["content"] = "这次查询没有成功。"
                await self._save_assistant_message(repository, session_id, assistant)
                saved = True
        finally:
            if repository is not None and session_id and not saved:
                await repository.update_session_meta(session_id, None, _now_ms())

    # ------------------------------------------------------------------ 快捷路径

    async def _generate_ddl_sql(self, query: str) -> str:
        """让 LLM 根据用户描述生成建表/删表 DDL，供用户复制到 dw 数仓手动执行"""
        prompt = PromptTemplate(
            template=load_prompt("generate_ddl"), input_variables=["query"]
        )
        output_parser = StrOutputParser()
        chain = prompt | llm | output_parser
        result = (await chain.ainvoke({"query": query})).strip()
        # 只接受以 CREATE/DROP/ALTER TABLE 开头的纯 DDL，防止模型输出解释文字
        if not re.match(
            r"^\s*(CREATE\s+TABLE|DROP\s+TABLE|ALTER\s+TABLE)", result, re.IGNORECASE
        ):
            raise ValueError("LLM 未生成合法的 DDL 语句")
        return result

    async def _handle_table_meta_query(self, query: str) -> dict:
        """表元数据快捷查询：直接查 dw 库所有表，组装 assistant 落库数据

        - 表数量不超过阈值：返回所有表 + 每张表全部数据（多表结构）
        - 表数量超过阈值：不返回数据，只提示用户去数据库查询，并给出 SQL
        """
        tables = await self.dw_mysql_repository.list_tables()

        if len(tables) <= TABLE_LIST_THRESHOLD:
            groups = []
            for table in tables:
                rows = await self.dw_mysql_repository.fetch_table_data(table)
                groups.append({"表名": table, "行数": len(rows), "数据": _json_safe(rows)})
            # 展示实际执行的 SQL：先看表清单，再逐表查全量数据
            sql_lines = ["SHOW TABLES;"]
            sql_lines.extend(f"SELECT * FROM `{table}`;" for table in tables)
            return {
                "steps": [],
                "content": f"查询完成，共 {len(tables)} 张表。",
                "sql": "\n".join(sql_lines),
                "result_summary": groups,
                "error": None,
            }

        return {
            "steps": [],
            "content": (
                f"数据库中共有 {len(tables)} 张表，数量较多，"
                "请到数据库中执行以下语句查看。"
            ),
            "sql": "SHOW TABLES;",
            "result_summary": [],
            "error": None,
        }

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
            # 用户点击"取消不执行"：落库为已取消，而非默认的"流程已结束"
            if step == "写操作已取消":
                assistant["content"] = "已取消，未执行。"
        elif ctype == "result":
            data = chunk.get("data") or []
            assistant["sql"] = chunk.get("sql")
            # 写操作执行成功且审计落库后，记录 audit_log_id，供消息落库时挂接回滚入口
            if chunk.get("audit_log_id"):
                assistant["audit_log_id"] = chunk["audit_log_id"]
            if isinstance(data, list):
                if data and isinstance(data[0], dict) and "数据" in data[0]:
                    # 多表结构（表元数据快捷查询 / INSERT 结果区展示目标表）
                    total = sum(g.get("行数", 0) for g in data)
                    names = "、".join(str(g.get("表名", "")) for g in data)
                    assistant["content"] = f"查询完成，共 {total} 行（{names}）。"
                    assistant["result_summary"] = _json_safe(data[:10])
                else:
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
                audit_log_id=assistant.get("audit_log_id"),
                created_at=_now_ms(),
            )
        )
        await repository.update_session_meta(session_id, None, _now_ms())
