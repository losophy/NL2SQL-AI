/**
 * 智能体接口客户端
 * 封装后端 /api/query SSE 流式接口请求与事件解析逻辑
 */
import type {
  AgentEvent,
  AuditLog,
  RollbackResult,
  SessionDetail,
  SessionSummary,
} from "../types/agent";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") ?? "";

type QueryOptions = {
  signal?: AbortSignal;
  /** 会话 id：传了则本次问数记录归属到该会话 */
  sessionId?: string;
  onEvent: (event: AgentEvent) => void;
};

export async function streamQuery(query: string, options: QueryOptions) {
  const response = await fetch(`${API_BASE_URL}/api/query`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify({ query, session_id: options.sessionId }),
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`接口请求失败：HTTP ${response.status}`);
  }

  if (!response.body) {
    throw new Error("浏览器未返回可读取的流式响应。");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split(/\n\n/);
    buffer = chunks.pop() ?? "";

    for (const chunk of chunks) {
      const event = parseSseChunk(chunk);
      if (event) {
        options.onEvent(event);
      }
    }
  }

  buffer += decoder.decode();
  const tail = parseSseChunk(buffer);
  if (tail) {
    options.onEvent(tail);
  }
}

/** 写操作审批续流：把用户在审核卡片上的决策提交后端，返回恢复执行后的 SSE 流 */
export async function streamHumanFeedback(
  threadId: string,
  action: "approve" | "reject",
  sessionId: string | undefined,
  options: QueryOptions,
) {
  const response = await fetch(`${API_BASE_URL}/api/human-feedback`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify({ thread_id: threadId, action, session_id: sessionId }),
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`接口请求失败：HTTP ${response.status}`);
  }

  if (!response.body) {
    throw new Error("浏览器未返回可读取的流式响应。");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split(/\n\n/);
    buffer = chunks.pop() ?? "";

    for (const chunk of chunks) {
      const event = parseSseChunk(chunk);
      if (event) {
        options.onEvent(event);
      }
    }
  }

  buffer += decoder.decode();
  const tail = parseSseChunk(buffer);
  if (tail) {
    options.onEvent(tail);
  }
}

function parseSseChunk(chunk: string): AgentEvent | null {
  const payload = chunk
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.replace(/^data:\s?/, ""))
    .join("\n")
    .trim();

  if (!payload) return null;

  try {
    return JSON.parse(payload) as AgentEvent;
  } catch {
    return {
      type: "error",
      message: `无法解析后端事件：${payload}`,
    };
  }
}

// ------------------------------------------------------------------ 会话历史接口

export async function listSessions(): Promise<SessionSummary[]> {
  const response = await fetch(`${API_BASE_URL}/api/sessions`);
  if (!response.ok) throw new Error(`获取会话列表失败：HTTP ${response.status}`);
  return response.json();
}

export async function createSession(): Promise<SessionSummary> {
  const response = await fetch(`${API_BASE_URL}/api/sessions`, { method: "POST" });
  if (!response.ok) throw new Error(`创建会话失败：HTTP ${response.status}`);
  return response.json();
}

export async function getSession(sessionId: string): Promise<SessionDetail> {
  const response = await fetch(`${API_BASE_URL}/api/sessions/${sessionId}`);
  if (!response.ok) throw new Error(`获取会话详情失败：HTTP ${response.status}`);
  return response.json();
}

export async function deleteSession(sessionId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/sessions/${sessionId}`, {
    method: "DELETE",
  });
  if (!response.ok && response.status !== 404) {
    throw new Error(`删除会话失败：HTTP ${response.status}`);
  }
}

// ------------------------------------------------------------------ Time-Travel 回滚接口

/** 拉取某会话的写操作审计日志（回滚列表数据源） */
export async function listAuditLogs(sessionId: string): Promise<AuditLog[]> {
  const response = await fetch(
    `${API_BASE_URL}/api/audit-logs?session_id=${encodeURIComponent(sessionId)}`,
  );
  if (!response.ok) throw new Error(`获取回滚历史失败：HTTP ${response.status}`);
  return response.json();
}

/** 回滚目标写操作（及其之后的所有操作），返回回滚摘要 */
export async function rollback(logId: number): Promise<RollbackResult> {
  const response = await fetch(`${API_BASE_URL}/api/rollback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ log_id: logId }),
  });
  if (!response.ok) {
    let detail = `回滚失败：HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // 非 JSON 响应保持默认错误信息
    }
    throw new Error(detail);
  }
  return response.json();
}
