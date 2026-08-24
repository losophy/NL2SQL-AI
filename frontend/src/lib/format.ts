/**
 * 通用格式化工具
 * 提供 className 合并、时间格式化和查询结果文本化等通用工具函数
 */
export function cn(...classes: Array<string | false | null | undefined>) {
  return classes.filter(Boolean).join(" ");
}

export function formatTime(timestamp: number) {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(timestamp);
}

export function summarizeResult(data: unknown) {
  if (Array.isArray(data)) {
    return data.length > 0 ? `查询完成，共 ${data.length} 行结果。` : "查询完成，结果为空。";
  }

  if (data && typeof data === "object") {
    return "查询完成，已返回结构化结果。";
  }

  if (data === null || data === undefined || data === "") {
    return "查询完成，结果为空。";
  }

  return `查询完成：${String(data)}`;
}

export function toClipboardText(value: unknown) {
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

/**
 * 按 SQL 关键字分行（轻量 SQL 美化，零依赖）
 * SELECT / FROM / WHERE / GROUP BY / ORDER BY / JOIN 等关键字各自独立成行，
 * AND / OR / ON 另起一行并缩进，便于阅读生成的 SQL。
 */
const SQL_KEYWORD_RE =
  /\b(SELECT|FROM|WHERE|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|LEFT\s+JOIN|RIGHT\s+JOIN|INNER\s+JOIN|FULL\s+JOIN|JOIN|UNION\s+ALL|UNION)\b/gi;
const SQL_CLAUSE_RE = /\b(AND|OR|ON)\b/gi;

export function formatSql(sql: string): string {
  if (!sql) return sql;

  let out = sql.trim();

  // 主关键字前换行并统一大写
  out = out.replace(SQL_KEYWORD_RE, (match) => `\n${match.trim().toUpperCase()}`);

  // 子句（AND / OR / ON）换行并缩进两级
  out = out.replace(SQL_CLAUSE_RE, (match) => `\n  ${match.toUpperCase()}`);

  // 清理连续空行与首行前导空行
  out = out.replace(/\n{2,}/g, "\n").trim();

  return out;
}
