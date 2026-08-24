/**
 * 聊天消息气泡组件
 * 组合展示用户问题、智能体回复、执行流程和结果表格
 */
import { Bot, Copy, UserRound } from "lucide-react";
import { ResultTable } from "./ResultTable";
import { StepRail } from "./StepRail";
import { cn, formatSql, formatTime, toClipboardText } from "../lib/format";
import { isTableDataGroups } from "../types/agent";
import type { ChatMessage } from "../types/agent";

export function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";

  const copy = async () => {
    const text = message.result ? toClipboardText(message.result) : message.content;
    await navigator.clipboard.writeText(text);
  };

  return (
    <article className={cn("group flex gap-3", isUser && "justify-end")}>
      {!isUser && (
        <div className="mt-1 grid h-9 w-9 shrink-0 place-items-center rounded-full bg-ink text-parchment">
          <Bot className="h-4 w-4" aria-hidden="true" />
        </div>
      )}

      <div className={cn("max-w-[920px] flex-1", isUser && "flex max-w-[760px] justify-end")}>
        <div
          className={cn(
            "relative border px-5 py-4 shadow-line",
            isUser
              ? "border-ink/80 bg-ink text-parchment"
              : "border-ink/10 bg-[#f0f7ef]/85 text-ink backdrop-blur",
          )}
        >
          <div className="flex items-start justify-between gap-3">
            <p className="whitespace-pre-wrap text-[15px] leading-7">{message.content}</p>
            {!isUser && message.status !== "streaming" && (
              <button
                type="button"
                onClick={copy}
                className="shrink-0 rounded-full p-1.5 text-ink/45 opacity-0 outline-none transition hover:bg-ink/5 hover:text-ink focus:opacity-100 focus:ring-2 focus:ring-moss/40 group-hover:opacity-100"
                title="复制"
                aria-label="复制"
              >
                <Copy className="h-4 w-4" aria-hidden="true" />
              </button>
            )}
          </div>

          {message.error && (
            <div className="mt-3 border border-tomato/30 bg-tomato/10 px-3 py-2 text-sm text-tomato">
              {message.error}
            </div>
          )}

          {!isUser && <StepRail steps={message.steps} />}
          {!isUser && message.sql && (
            <div className="mt-3 border border-ink/10 bg-white/60">
              <div className="border-b border-ink/10 px-3 py-2 text-xs font-semibold uppercase tracking-[0.16em] text-ink/45">
                执行的SQL语句
              </div>
              <pre className="whitespace-pre-wrap break-words px-3 py-2.5 font-mono text-[13px] leading-6 text-ink/85">
                {formatSql(message.sql)}
              </pre>
            </div>
          )}
          {!isUser && message.result !== undefined && isTableDataGroups(message.result) && (
            <div className="mt-3 space-y-4">
              {message.result.map((group) => (
                <div key={group.表名}>
                  <div className="mb-1 text-sm font-semibold text-ink/70">
                    {group.表名}（{group.行数} 行）
                  </div>
                  <ResultTable data={group.数据} />
                </div>
              ))}
            </div>
          )}
          {!isUser && message.result !== undefined && !isTableDataGroups(message.result) && (
            <ResultTable data={message.result} />
          )}

          <div
            className={cn(
              "mt-3 text-xs",
              isUser ? "text-parchment/55" : "text-ink/45",
            )}
          >
            {formatTime(message.createdAt)}
          </div>
        </div>
      </div>

      {isUser && (
        <div className="mt-1 grid h-9 w-9 shrink-0 place-items-center rounded-full bg-moss text-white">
          <UserRound className="h-4 w-4" aria-hidden="true" />
        </div>
      )}
    </article>
  );
}
