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
  /** 写操作执行成功且审计落库后的记录 id：前端据此在消息左侧挂接回滚入口 */
  audit_log_id?: number;
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

/** 写操作触发人工审批：后端暂停流程，等待用户在审核卡片上确认/取消 */
export type HumanApprovalEvent = {
  type: "human_approval";
  sql: string;
  sql_type: string;
  impact_summary: string;
  thread_id: string;
};

export type AgentEvent =
  | ProgressEvent
  | ResultEvent
  | ErrorEvent
  | SessionCreatedEvent
  | HumanApprovalEvent;

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
  status?: "streaming" | "done" | "error" | "waiting";
  steps?: StepState[];
  result?: unknown;
  /** 最终执行的 SQL 语句（来自 result 事件） */
  sql?: string;
  error?: string;
  /** 写操作待审批信息：非空时渲染审核卡片 */
  pendingApproval?: {
    sql: string;
    sql_type: string;
    impact_summary: string;
    thread_id: string;
  };
  /** 写操作执行成功后的审计记录 id：非空时消息左侧展示回滚入口 */
  auditLogId?: number;
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
  audit_log_id?: number | null;
  created_at: number;
};

/** 会话详情 */
export type SessionDetail = SessionSummary & {
  messages: SessionMessage[];
};

/** 表元数据快捷查询返回的“一张表 + 其全部数据”分组 */
export type TableDataGroup = {
  表名: string;
  行数: number;
  数据: Record<string, unknown>[];
};

/** 判断 result 是否为表元数据多表结构（元素含"数据"字段） */
export function isTableDataGroups(result: unknown): result is TableDataGroup[] {
  return (
    Array.isArray(result) &&
    result.length > 0 &&
    typeof result[0] === "object" &&
    result[0] !== null &&
    Array.isArray((result[0] as TableDataGroup).数据)
  );
}

// ------------------------------------------------------------------ Time-Travel 回滚

/** 写操作审计记录（GET /api/audit-logs 返回，前端回滚列表数据源） */
export type AuditLog = {
  log_id: number;
  session_id: string | null;
  seq: number;
  op_type: "INSERT" | "UPDATE" | "DELETE";
  table_name: string;
  sql_text: string;
  before_data: Record<string, unknown>[] | null;
  row_count: number;
  status: "committed" | "rolled_back";
  created_at: string | null;
};

/** 回滚结果摘要（POST /api/rollback 返回） */
export type RollbackResult = {
  log_id: number;
  rolled_back: number;
  restored_rows: number;
  descriptions: string[];
  /** 本次实际被回滚（含连带）的审计记录 id 列表 */
  rolled_back_log_ids: number[];
};
