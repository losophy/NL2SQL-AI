/**
 * 前端应用主组件
 * 负责聊天会话状态、SSE 事件消费、会话历史持久化和整体页面布局
 */
import {
  Activity,
  BarChart3,
  Eraser,
  Leaf,
  MessageSquarePlus,
  Server,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Composer } from "./components/Composer";
import { EmptyState } from "./components/EmptyState";
import { MessageBubble } from "./components/MessageBubble";
import { SessionList } from "./components/SessionList";
import {
  createSession,
  deleteSession,
  getSession,
  listAuditLogs,
  listSessions,
  rollback,
  streamHumanFeedback,
  streamQuery,
} from "./lib/agentApi";
import { cn, summarizeResult } from "./lib/format";
import type {
  AgentEvent,
  AuditLog,
  ChatMessage,
  SessionSummary,
  StepState,
} from "./types/agent";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "Vite /api proxy";

function makeId() {
  return crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function upsertStep(steps: StepState[] = [], event: Extract<AgentEvent, { type: "progress" }>) {
  const next = steps.filter((item) => item.step !== event.step);
  next.push({
    step: event.step,
    status: event.status,
    updatedAt: Date.now(),
  });
  return next;
}

export default function App() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [activeController, setActiveController] = useState<AbortController | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  /** 当前会话的写操作审计日志（消息左侧回滚卡片数据源），问数结束后刷新 */
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  /** 回滚请求进行中的审计 id（用于禁用所有回滚按钮） */
  const [rollingLogId, setRollingLogId] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const isStreaming = Boolean(activeController);
  const canSubmit = draft.trim().length > 0 && !isStreaming;

  const completedCount = useMemo(
    () => messages.filter((message) => message.role === "assistant" && message.status === "done").length,
    [messages],
  );

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages]);

  useEffect(() => {
    void refreshSessions();
    // 挂载时加载一次会话列表即可
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function refreshSessions() {
    try {
      const list = await listSessions();
      setSessions(list);
    } catch {
      // 列表刷新失败不影响主流程
    }
  }

  /** 拉取当前会话的写操作审计日志（供消息左侧回滚卡片匹配状态） */
  const loadAuditLogs = async (sessionId: string | null) => {
    if (!sessionId) {
      setAuditLogs([]);
      return;
    }
    try {
      const list = await listAuditLogs(sessionId);
      setAuditLogs(list);
    } catch {
      // 审计列表拉取失败不影响聊天主流程
    }
  };

  /** 消息级回滚：二次确认后调用回滚接口，并按返回的 log_id 列表更新消息状态 */
  const handleRollback = async (logId: number) => {
    if (rollingLogId !== null) return;
    const ok = window.confirm(
      "确认回滚该写操作？\n\n该操作之后的所有写操作也会一并还原（LIFO 逆序），请确认数据影响。",
    );
    if (!ok) return;
    setRollingLogId(logId);
    try {
      const result = await rollback(logId);
      // 刷新审计列表：被回滚（含连带）记录的 status 变为 rolled_back，消息左侧卡片自动变"已回滚"
      await loadAuditLogs(activeSessionId);
      window.alert(
        `已回滚 ${result.rolled_back} 个操作，还原 ${result.restored_rows} 行。\n\n${result.descriptions.join("\n")}`,
      );
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "回滚失败");
    } finally {
      setRollingLogId(null);
    }
  };

  const startNewSession = async () => {
    if (isStreaming) return;
    setMessages([]);
    setDraft("");
    try {
      const created = await createSession();
      setActiveSessionId(created.id);
      setSessions((current) => [created, ...current]);
    } catch {
      setActiveSessionId(null);
    }
  };

  const selectSession = async (sessionId: string) => {
    if (isStreaming) return;
    try {
      const detail = await getSession(sessionId);
      setActiveSessionId(sessionId);
      setDraft("");
      setMessages(
        detail.messages.map((m) => ({
          id: m.id,
          role: m.role,
          content: m.content,
          createdAt: m.created_at,
          status: m.role === "assistant" ? (m.error ? "error" : "done") : undefined,
          steps: m.steps
            ? m.steps.map((step) => ({ ...step, updatedAt: m.created_at }))
            : undefined,
          result: m.result_summary ?? undefined,
          sql: m.sql ?? undefined,
          error: m.error ?? undefined,
          auditLogId: m.audit_log_id ?? undefined,
        })),
      );
      await loadAuditLogs(sessionId);
    } catch {
      // 详情加载失败保持现状
    }
  };

  const handleDeleteSession = async (sessionId: string) => {
    if (isStreaming) return;
    try {
      await deleteSession(sessionId);
      setSessions((current) => current.filter((item) => item.id !== sessionId));
      if (activeSessionId === sessionId) {
        setActiveSessionId(null);
        setMessages([]);
        setDraft("");
      }
    } catch {
      // 删除失败忽略
    }
  };

  const startQuery = async (rawQuery = draft) => {
    const query = rawQuery.trim();
    if (!query || isStreaming) return;

    // 确保存在会话：没有活跃会话时先创建一个
    let sessionId: string | undefined = activeSessionId ?? undefined;
    if (!sessionId) {
      try {
        const created = await createSession();
        sessionId = created.id;
        setActiveSessionId(sessionId);
        setSessions((current) => [created, ...current]);
      } catch {
        // 创建失败不阻塞问数（后端兜底会在流尾回传 session_created）
      }
    }

    const userMessage: ChatMessage = {
      id: makeId(),
      role: "user",
      content: query,
      createdAt: Date.now(),
    };

    const assistantId = makeId();
    const assistantMessage: ChatMessage = {
      id: assistantId,
      role: "assistant",
      content: "正在连接问数智能体...",
      createdAt: Date.now(),
      status: "streaming",
      steps: [],
    };

    const controller = new AbortController();
    setActiveController(controller);
    setDraft("");
    setMessages((current) => [...current, userMessage, assistantMessage]);

    const onEvent = (event: AgentEvent) => {
      setMessages((current) =>
        current.map((message) => {
          if (message.id !== assistantId) return message;

          if (event.type === "session_created") {
            setActiveSessionId(event.session_id);
            return message;
          }

          if (event.type === "human_approval") {
            return {
              ...message,
              status: "waiting",
              pendingApproval: {
                sql: event.sql,
                sql_type: event.sql_type,
                impact_summary: event.impact_summary,
                thread_id: event.thread_id,
              },
            };
          }

          if (event.type === "progress") {
            return {
              ...message,
              content: event.status === "running" ? `正在执行：${event.step}` : message.content,
              steps: upsertStep(message.steps, event),
            };
          }

          if (event.type === "result") {
            return {
              ...message,
              status: "done",
              content: summarizeResult(event.data),
              result: event.data,
              sql: event.sql,
              auditLogId: event.audit_log_id,
            };
          }

          return {
            ...message,
            status: "error",
            content: "这次查询没有成功。",
            error: event.message,
          };
        }),
      );
    };

    try {
      await streamQuery(query, {
        signal: controller.signal,
        sessionId,
        onEvent,
      });
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId && message.status === "streaming"
            ? { ...message, status: "done", content: "流程已结束，后端未返回查询结果。" }
            : message,
        ),
      );
    } catch (error) {
      const isAbort = error instanceof DOMException && error.name === "AbortError";
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId
            ? {
                ...message,
                status: isAbort ? "done" : "error",
                content: isAbort ? "已停止本次查询。" : "无法连接问数接口。",
                error: isAbort ? undefined : error instanceof Error ? error.message : String(error),
              }
            : message,
        ),
      );
    } finally {
      setActiveController(null);
      // 问数结束后刷新列表（标题/更新时间可能变化）
      void refreshSessions();
      // 写操作可能已落库审计，刷新回滚面板
      setAuditTick((tick) => tick + 1);
    }
  };

  const stopQuery = () => {
    activeController?.abort();
  };

  /** 用户在审核卡片上确认/取消写操作：提交决策并续流恢复执行 */
  const handleApproval = async (threadId: string, action: "approve" | "reject") => {
    if (isStreaming) return;
    const target = messages.find((message) => message.role === "assistant" && message.pendingApproval);
    if (!target) return;

    // 取消不执行：收回审批卡片但保留消息（含执行流程），并通知后端取消（不再重新生成、不执行）
    if (action === "reject") {
      setMessages((current) =>
        current.map((message) =>
          message.id === target.id
            ? {
                ...message,
                pendingApproval: undefined,
                status: "done",
                content: "已取消，未执行。",
              }
            : message,
        ),
      );
      const cancelController = new AbortController();
      setActiveController(cancelController);
      // 取消不执行：仍需消费续流事件，把"等待人工确认"标记为完成、展示"写操作已取消"，
      // 否则该步骤的圈会一直转（完成事件随续流下发，不能完全忽略）
      const onCancelEvent = (event: AgentEvent) => {
        setMessages((current) =>
          current.map((message) => {
            if (message.id !== target.id) return message;
            // 审批卡片已本地收回：忽略续流重发的审批事件
            if (event.type === "human_approval") return message;
            if (event.type === "progress") {
              return {
                ...message,
                content:
                  event.status === "running" ? `正在执行：${event.step}` : message.content,
                steps: upsertStep(message.steps, event),
              };
            }
            return message;
          }),
        );
      };
      try {
        await streamHumanFeedback(threadId, action, activeSessionId ?? undefined, {
          signal: cancelController.signal,
          onEvent: onCancelEvent,
        });
      } catch {
        // 取消请求失败不影响界面
      } finally {
        setActiveController(null);
        void refreshSessions();
        void loadAuditLogs(activeSessionId);
      }
      return;
    }

    const controller = new AbortController();
    setActiveController(controller);
    setMessages((current) =>
      current.map((message) =>
        message.id === target.id
          ? {
              ...message,
              pendingApproval: undefined,
              status: "streaming",
              content: "正在执行写操作...",
            }
          : message,
      ),
    );

    const onEvent = (event: AgentEvent) => {
      setMessages((current) =>
        current.map((message) => {
          if (message.id !== target.id) return message;

          if (event.type === "session_created") {
            setActiveSessionId(event.session_id);
            return message;
          }

          // 审批已提交，忽略续流时重发的审批事件（避免审批卡片重新弹出/残留）
          if (event.type === "human_approval") {
            return message;
          }

          if (event.type === "progress") {
            return {
              ...message,
              content: event.status === "running" ? `正在执行：${event.step}` : message.content,
              steps: upsertStep(message.steps, event),
            };
          }

          if (event.type === "result") {
            return {
              ...message,
              status: "done",
              content: summarizeResult(event.data),
              result: event.data,
              sql: event.sql,
              auditLogId: event.audit_log_id,
            };
          }

          return {
            ...message,
            status: "error",
            content: "这次查询没有成功。",
            error: event.message,
          };
        }),
      );
    };

    try {
      await streamHumanFeedback(threadId, action, activeSessionId ?? undefined, {
        signal: controller.signal,
        onEvent,
      });
      setMessages((current) =>
        current.map((message) =>
          message.id === target.id && message.status === "streaming"
            ? { ...message, status: "done", content: "流程已结束，后端未返回查询结果。" }
            : message,
        ),
      );
    } catch (error) {
      const isAbort = error instanceof DOMException && error.name === "AbortError";
      setMessages((current) =>
        current.map((message) =>
          message.id === target.id
            ? {
                ...message,
                status: isAbort ? "done" : "error",
                content: isAbort ? "已停止本次查询。" : "无法连接问数接口。",
                error: isAbort ? undefined : error instanceof Error ? error.message : String(error),
              }
            : message,
        ),
      );
    } finally {
      setActiveController(null);
      void refreshSessions();
      // 问数/审批结束后刷新审计列表，消息左侧回滚卡片状态随之更新
      void loadAuditLogs(activeSessionId);
    }
  };

  return (
    <div className="h-dvh overflow-hidden bg-parchment text-ink">
      <div className="pointer-events-none fixed inset-0 bg-[linear-gradient(90deg,rgba(44,58,49,0.045)_1px,transparent_1px),linear-gradient(rgba(44,58,49,0.035)_1px,transparent_1px)] bg-[size:48px_48px]" />
      <div className="pointer-events-none fixed inset-0 grain" />

      <div className="relative grid h-full min-h-0 overflow-hidden lg:grid-cols-[300px_minmax(0,1fr)]">
        <aside className="hidden min-h-0 border-r border-ink/10 bg-[#e4eee1]/85 backdrop-blur lg:flex lg:flex-col">
          <div className="border-b border-ink/10 px-5 py-5">
            <div className="flex items-center gap-3">
              <div className="grid h-10 w-10 place-items-center bg-ink text-parchment">
                <BarChart3 className="h-5 w-5" aria-hidden="true" />
              </div>
              <div>
                <div className="text-base font-semibold tracking-[0.02em]">自然语言到SQL</div>
                <div className="text-xs text-ink/50">NL2SQL-agent</div>
              </div>
            </div>
          </div>

          <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-4">
            <button
              type="button"
              onClick={() => void startNewSession()}
              disabled={isStreaming}
              className="flex h-11 w-full items-center justify-center gap-2 bg-ink text-sm font-semibold text-parchment transition hover:bg-soot disabled:cursor-not-allowed disabled:bg-ink/35"
            >
              <MessageSquarePlus className="h-4 w-4" aria-hidden="true" />
              新会话
            </button>

            <SessionList
              sessions={sessions}
              activeId={activeSessionId}
              disabled={isStreaming}
              onSelect={(id) => void selectSession(id)}
              onDelete={(id) => void handleDeleteSession(id)}
            />
          </div>

          <div className="border-t border-ink/10 p-4">
            <div className="grid gap-2 text-xs text-ink/55">
              <div className="flex items-center justify-between gap-3">
                <span className="inline-flex items-center gap-2">
                  <Server className="h-3.5 w-3.5" aria-hidden="true" />
                  API
                </span>
                <span className="truncate font-mono">{API_BASE_URL}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="inline-flex items-center gap-2">
                  <Activity className="h-3.5 w-3.5" aria-hidden="true" />
                  完成
                </span>
                <span>{completedCount}</span>
              </div>
            </div>
          </div>
        </aside>

        <main className="flex min-h-0 min-w-0 flex-col overflow-hidden">
          <header className="flex h-16 shrink-0 items-center justify-between border-b border-ink/10 bg-parchment/88 px-4 backdrop-blur lg:px-6">
            <div className="flex min-w-0 items-center gap-3">
              <div className="grid h-9 w-9 shrink-0 place-items-center bg-moss text-white lg:hidden">
                <BarChart3 className="h-4 w-4" aria-hidden="true" />
              </div>
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-ink">自然语言到SQL Agent</div>
                <div className="truncate text-xs text-ink/45">FastAPI SSE / LangGraph</div>
              </div>
            </div>
            <button
              type="button"
              onClick={() => void startNewSession()}
              disabled={isStreaming}
              className={cn(
                "grid h-9 w-9 place-items-center rounded-full text-ink/55 transition hover:bg-ink/5 hover:text-ink disabled:cursor-not-allowed disabled:opacity-35",
              )}
              title="新建会话"
              aria-label="新建会话"
            >
              <Eraser className="h-4 w-4" aria-hidden="true" />
            </button>
          </header>

          <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
            {messages.length === 0 ? (
              <EmptyState />
            ) : (
              <div className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-6 lg:px-8">
                {messages.map((message) => (
                  <MessageBubble
                    key={message.id}
                    message={message}
                    approvalBusy={isStreaming}
                    onApprove={(threadId) => void handleApproval(threadId, "approve")}
                    onReject={(threadId) => void handleApproval(threadId, "reject")}
                    auditLog={
                      message.auditLogId !== undefined
                        ? auditLogs.find((log) => log.log_id === message.auditLogId)
                        : undefined
                    }
                    rollingLogId={rollingLogId}
                    onRollback={(logId) => void handleRollback(logId)}
                  />
                ))}
              </div>
            )}
          </div>

          <div className="border-t border-ink/10 bg-[#e6eee3]/45 px-4 py-2 text-center text-xs text-ink/45">
            <span className="inline-flex items-center gap-2">
              <Leaf className="h-3.5 w-3.5 text-moss" aria-hidden="true" />
              {isStreaming ? "运行中" : "就绪"}
            </span>
          </div>
          <Composer
            value={draft}
            disabled={!canSubmit}
            isStreaming={isStreaming}
            onChange={setDraft}
            onSubmit={() => startQuery()}
            onStop={stopQuery}
          />
        </main>
      </div>
    </div>
  );
}
