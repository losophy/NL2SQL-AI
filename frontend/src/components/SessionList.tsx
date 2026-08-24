/**
 * 历史会话列表组件
 * 展示会话历史，支持点击切换与单条删除
 */
import { Trash2 } from "lucide-react";
import { cn } from "../lib/format";
import type { SessionSummary } from "../types/agent";

type SessionListProps = {
  sessions: SessionSummary[];
  activeId: string | null;
  disabled?: boolean;
  onSelect: (sessionId: string) => void;
  onDelete: (sessionId: string) => void;
};

export function SessionList({
  sessions,
  activeId,
  disabled,
  onSelect,
  onDelete,
}: SessionListProps) {
  if (sessions.length === 0) return null;

  return (
    <section>
      <div className="mb-2 px-1 text-xs font-semibold uppercase tracking-[0.16em] text-ink/45">
        历史会话
      </div>
      <div className="space-y-2">
        {sessions.map((session) => {
          const active = session.id === activeId;
          return (
            <div
              key={session.id}
              className={cn(
                "group flex items-center gap-1 border px-3 py-2.5 transition",
                active
                  ? "border-moss/50 bg-white/75"
                  : "border-ink/10 bg-white/42 hover:border-moss/35 hover:bg-white/75",
                disabled && "pointer-events-none opacity-55",
              )}
            >
              <button
                type="button"
                onClick={() => onSelect(session.id)}
                disabled={disabled}
                className="min-w-0 flex-1 truncate text-left text-sm leading-5 text-ink/75 transition hover:text-ink disabled:cursor-not-allowed"
                title={session.title ?? "新会话"}
              >
                {session.title || "新会话"}
              </button>
              <button
                type="button"
                onClick={() => onDelete(session.id)}
                disabled={disabled}
                className="shrink-0 rounded p-1 text-ink/35 opacity-0 transition hover:bg-tomato/10 hover:text-tomato focus:opacity-100 group-hover:opacity-100 disabled:cursor-not-allowed"
                title="删除会话"
                aria-label="删除会话"
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            </div>
          );
        })}
      </div>
    </section>
  );
}
