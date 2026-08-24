/**
 * 智能体类型定义
 * 定义问数智能体前端使用的 SSE 事件、流程步骤和聊天消息类型
 */
export type ProgressStatus = "running" | "success" | "error";

export type ProgressEvent = {
  type: "progress";
  step: string;
  status: ProgressStatus;
};

export type ResultEvent = {
  type: "result";
  data: unknown;
  /** 最终执行的 SQL 语句，由 run_sql 节点随结果一并下发 */
  sql?: string;
};

export type ErrorEvent = {
  type: "error";
  message: string;
};

/** 后端兜底创建会话时通过 SSE 尾事件回传的会话 id */
export type SessionCreatedEvent = {
  type: "session_created";
  session_id: string;
};

export type AgentEvent = ProgressEvent | ResultEvent | ErrorEvent | SessionCreatedEvent;

export type StepState = {
  step: string;
  status: ProgressStatus;
  updatedAt: number;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: number;
  status?: "streaming" | "done" | "error";
  steps?: StepState[];
  result?: unknown;
  /** 最终执行的 SQL 语句（来自 result 事件） */
  sql?: string;
  error?: string;
};

/** 会话列表项（后端 /api/sessions 返回） */
export type SessionSummary = {
  id: string;
  title: string | null;
  created_at: number;
  updated_at: number;
};

/** 会话详情里的单条历史消息 */
export type SessionMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  steps?: StepState[] | null;
  sql?: string | null;
  result_summary?: unknown[] | null;
  error?: string | null;
  created_at: number;
};

/** 会话详情 */
export type SessionDetail = SessionSummary & {
  messages: SessionMessage[];
};
