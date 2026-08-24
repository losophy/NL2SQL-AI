/**
 * 消息级回滚卡片（Time-Travel）
 * 渲染在写操作 assistant 消息的左侧：展示操作类型/表/行数 + 回滚按钮
 * 点击回滚会连带逆序还原该操作之后的所有写操作
 */
import { Loader2, RotateCcw } from "lucide-react";
import type { AuditLog } from "../types/agent";
import { cn } from "../lib/format";

const OP_LABEL: Record<string, { text: string; cls: string }> = {
  INSERT: { text: "新增", cls: "bg-moss/15 text-moss" },
  UPDATE: { text: "修改", cls: "bg-brass/20 text-brass" },
  DELETE: { text: "删除", cls: "bg-tomato/15 text-tomato" },
};

type RollbackBadgeProps = {
  /** 该消息对应的审计记录（App 按 auditLogId 从会话审计列表匹配） */
  auditLog?: AuditLog;
  /** 回滚进行中（禁止并发点击） */
  rolling?: boolean;
  onRollback?: (logId: number) => void;
};

export function RollbackBadge({ auditLog, rolling, onRollback }: RollbackBadgeProps) {
  if (!auditLog) return null;

  const label = OP_LABEL[auditLog.op_type] ?? {
    text: auditLog.op_type,
    cls: "bg-ink/10 text-ink/60",
  };
  const rolled = auditLog.status === "rolled_back";

  return (
    <div className="flex w-[84px] shrink-0 flex-col items-stretch gap-1.5 border border-ink/10 bg-white/50 px-2 py-2 text-center shadow-line">
      <div className="flex items-center justify-center gap-1">
        <span
          className={cn(
            "rounded-sm px-1.5 py-0.5 text-[11px] font-semibold",
            label.cls,
          )}
        >
          {label.text}
        </span>
        {rolled && (
          <span className="text-[10px] text-ink/40">已回滚</span>
        )}
      </div>
      <div className="truncate text-[11px] font-medium text-ink/80" title={auditLog.table_name}>
        {auditLog.table_name}
      </div>
      <div className="text-[10px] text-ink/45">{auditLog.row_count} 行</div>
      <button
        type="button"
        disabled={rolled || rolling === true}
        onClick={() => onRollback?.(auditLog.log_id)}
        className={cn(
          "mt-0.5 flex items-center justify-center gap-1 rounded border px-1.5 py-1 text-[11px] font-semibold transition",
          rolled
            ? "cursor-not-allowed border-ink/10 bg-white/30 text-ink/35"
            : "border-moss/30 bg-moss/10 text-moss hover:bg-moss/20",
          rolling === true && "cursor-not-allowed opacity-45",
        )}
        title={
          rolled
            ? "该操作已回滚"
            : "回滚到该操作之前（连带还原之后的所有写操作）"
        }
      >
        {rolling ? (
          <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
        ) : (
          <RotateCcw className="h-3 w-3" aria-hidden="true" />
        )}
        {rolled ? "已回滚" : "回滚"}
      </button>
    </div>
  );
}
