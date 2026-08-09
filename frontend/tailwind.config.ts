/**
 * Tailwind CSS 主题配置
 * 定义前端项目的字体、颜色和阴影扩展
 */
import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          '"LXGW WenKai Screen"',
          '"Noto Sans SC"',
          '"PingFang SC"',
          '"Microsoft YaHei"',
          "sans-serif",
        ],
        mono: ['"JetBrains Mono"', '"SFMono-Regular"', "Consolas", "monospace"],
      },
      colors: {
        parchment: "#deead6",
        ink: "#2c3a31",
        soot: "#232d26",
        moss: "#4a7a58",
        brass: "#93a06a",
        tomato: "#c4785f",
        mist: "#b9ceba",
      },
      boxShadow: {
        line: "0 1px 0 rgba(32, 32, 29, 0.08)",
        panel: "0 24px 70px rgba(45, 41, 37, 0.16)",
      },
    },
  },
  plugins: [],
} satisfies Config;
