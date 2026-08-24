/**
 * 写操作人工审批卡片
 * 展示待审批 SQL、操作类型与影响范围，提供确认执行/取消按钮
 */
import { AlertTriangle, Check, X } from "lucide-react";
import { formatSql } from "../lib/format";

type HumanApprovalCardProps = {
  sql: string;
  sqlType: string;
  impactSummary: string;
  disabled?: boolean;
  onApprove: () => void;
  onReject: () => void;
};

const TYPE_LABEL: Record<string, string> = {
  insert: "新增 INSERT",
  update: "修改 UPDATE",
  delete: "删除 DELETE",
  replace: "新增 REPLACE",
};

// 审批卡片标题按操作类型区分，而不是笼统的"写操作"
const TITLE_LABEL: Record<string, string> = {
  insert: "新增操作需要人工确认",
  update: "修改操作需要人工确认",
  delete: "删除操作需要人工确认",
  replace: "新增操作需要人工确认",
};

export function HumanApprovalCard({
  sql,
  sqlType,
  impactSummary,
  disabled,
  onApprove,
  onReject,
}: HumanApprovalCardProps) {
  return (
    <div className="mt-3 overflow-hidden border border-tomato/40 bg-tomato/5">
      <div className="flex items-center gap-2 border-b border-tomato/20 px-4 py-3">
        <AlertTriangle className="h-4 w-4 shrink-0 text-tomato" aria-hidden="true" />
        <div className="text-sm font-semibold text-tomato">
          {TITLE_LABEL[sqlType] ?? "写操作需要人工确认"}
        </div>
        <div className="ml-auto text-xs text-ink/55">
          {TYPE_LABEL[sqlType] ?? sqlType} · {impactSummary}
        </div>
      </div>

      <pre className="whitespace-pre-wrap break-words bg-white/60 px-4 py-3 font-mono text-[13px] leading-6 text-ink/85">
        {formatSql(sql)}
      </pre>

      <div className="flex items-center justify-end gap-2 border-t border-tomato/20 px-4 py-3">
        <button
          type="button"
          onClick={onReject}
          disabled={disabled}
          className="inline-flex h-8 items-center gap-1.5 border border-ink/15 px-3 text-sm text-ink/70 transition hover:bg-ink/5 disabled:cursor-not-allowed disabled:opacity-45"
        >
          <X className="h-3.5 w-3.5" aria-hidden="true" />
          取消不执行
        </button>
        <button
          type="button"
          onClick={onApprove}
          disabled={disabled}
          className="inline-flex h-8 items-center gap-1.5 bg-tomato px-3 text-sm font-semibold text-white transition hover:bg-tomato/90 disabled:cursor-not-allowed disabled:opacity-45"
        >
          <Check className="h-3.5 w-3.5" aria-hidden="true" />
          确认执行
        </button>
      </div>
    </div>
  );
}
