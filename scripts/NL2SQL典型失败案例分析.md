# NL2SQL-Agent 典型失败案例分析

> 来源：自动化评测（`scripts/run_eval.py`，63 条测试集）在真实运行中暴露的两类 NL2SQL 语义层失败。
> 两类均已修复并复测通过（qwen3-max-preview 完整轮：读操作 43/43，可执行率与结果正确率双 100%）。
> 判定明细与留档：`scripts/eval_report.json` / `scripts/eval_report.md`。

---

## 背景：为什么记录这两个案例

评测集的价值不在于"跑出高分"，而在于能够**稳定复现真实缺陷 → 定位根因层级 → 驱动修复 → 回归验证**。
这两个案例分别对应 NL2SQL 领域两类典型难点：

- **意图中的隐含语义**：用户问"哪些/有哪几种"时隐含"去重/枚举"的要求，但字面上没有出现；
- **对真实数据的忠实度**：WHERE 里的实体值（道具名、ID 等）必须与库中真实取值一致，不能凭记忆改写。

两个案例都满足"执行不报错、语法正确"，失败发生在语义层——这也是它们值得沉淀的原因。

---

## 案例一：枚举去重 ——"哪些"隐含去重语义（S29）

**用户问题**：*"苏苏都充值买过哪些道具？"*

**失败行为**：
```sql
SELECT item_name FROM fact_recharge f
JOIN dim_player p ON f.player_id = p.player_id
JOIN dim_item i ON f.item_id = i.item_id
WHERE p.player_name = '苏苏'
-- 返回 6 行：同一道具 I01 因被购买两次而重复出现
```

**期望**：`SELECT DISTINCT item_name ...` → 5 种道具。

**根因**：生成约束只告诉模型"表名列名不要编造、写操作要带 WHERE"，未覆盖"列举/枚举类问题要去重"的语义规则，模型按字面把 JOIN 出的明细原样返回。

**修复（两层）**：
1. Prompt 规则：列举/枚举意图（哪些/有哪/列举/列出/有哪几种）时结果必须唯一——目标实体列加 `DISTINCT` 或按维度 `GROUP BY`，禁止把重复明细当清单返回；
2. 系统兜底：`validate_sql.py` 的 `_ensure_distinct_for_enum`——当"问题含枚举意图词 + SQL 是单列、无聚合、无 DISTINCT/GROUP BY 的简单 SELECT"时，自动在列名前补 `DISTINCT` 并写回状态（只作用于最安全情形，多列/聚合/星号/非 SELECT 一律不碰，防误伤统计语义）。

**复测**：S29 ✓。

---

## 案例二：实体值一致性 —— WHERE 取值未忠实使用召回值（S35）

**用户问题**：*"S1 赛季通行证一共卖出多少份？"*

**失败行为**：
```sql
SELECT SUM(quantity) FROM fact_recharge f
JOIN dim_item i ON f.item_id = i.item_id
WHERE i.item_name = 'S1 赛季通行证'   -- ← 中间多了个空格
-- 库中真实值是 'S1赛季通行证'（无空格）→ 查不到，SUM 为 NULL
```

**根因**：WHERE 里的实体值不是从检索链路（ES 字段取值召回，会把真实枚举值喂给生成上下文）取的，而是模型凭记忆改写，把 `S1赛季通行证` 脑补成带空格的写法——"真实值已召回但未被强制使用"。

**修复**：Prompt 硬约束——WHERE 条件中的具体取值（枚举值/名称/ID/日期）只能使用上下文 `examples`（字段真实取值样例）提供的值，禁止自行拼接、增删空格、改写或臆造；上下文没有所需取值时用 `LIKE` 或反问用户，绝不编造。

**复测**：S35 ✓。同一条取值约束随后也拦截了写操作中的同类问题（用户要删 Z999、模型曾写成 C009，见写操作安全用例 W14）。

---

## 两案例的共性分析

| 维度 | 案例一（S29） | 案例二（S35） |
|---|---|---|
| 失败层面 | 语义（隐含意图） | 数据（实体值忠实） |
| 表象 | 结果多出重复行 | 结果为空/错误 |
| 技术归因 | 生成规则未覆盖枚举语义 | 生成规则未约束取值来源 |
| 修复手段 | Prompt 规则 + 校验层系统兜底 | Prompt 硬约束 |

**工程启示**：
- SQL 生成的失败大多不在"能否执行"，而在"意图理解"与"取值忠实"两个语义层面；
- 语义类问题适合"Prompt 规则（第一道）+ 系统确定性兜底（第二道）"的双层设计：规则让模型少犯，兜底让偶发也能被修；
- 评测集必须覆盖这类"能跑但语义错"的用例，否则高分没有意义。

---

## 附：修复过程中的一次"规则副作用"迭代（S01/S03/S42）

初版"列举去重"规则表述不精确（直接建议加 DISTINCT），曾诱导模型把 DISTINCT 用在 WHERE 过滤条件列上：

```sql
-- 错误示范（曾出现）
SELECT DISTINCT gender FROM dim_player WHERE gender='男'   -- 回答了"有哪些性别"而非"哪些男玩家"
SELECT COUNT(DISTINCT vip_level) FROM dim_player WHERE vip_level='VIP15'  -- 数错对象
```

二次修复：收紧规则表述——明确"去重只作用于用户要枚举的业务实体列（玩家行/道具行），严禁对过滤条件列去重后当答案"，并附反例说明。复测后 S01/S03/S42 全部通过。

**教训**：Prompt 规则的表述与约束边界会直接影响模型行为；每次规则改动都需跑一轮评测回归验证，防止"修 A 引入 B"。

---

## 相关代码位置

| 修复 | 位置 |
|---|---|
| 列举去重（Prompt 规则） | `prompts/generate_sql.prompt` 规则 4 |
| 列举去重（系统兜底） | `app/agent/nodes/validate_sql.py` → `_ensure_distinct_for_enum` |
| 取值忠实（Prompt 规则） | `prompts/generate_sql.prompt` 规则 5 |
| 评测与留档 | `scripts/run_eval.py`、`scripts/eval_report.json/md` |
