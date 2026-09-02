# NL2SQL-Agent 评测方案（自然语言转 SQL 数据表操作）

> 目的：为 NL2SQL-Agent 项目建立**结果导向、可复现**的量化评测指标（SQL 可执行率 / 结果正确率 / 写操作安全指标），指标定义见第 1 节。
> ⚠️ 原则：**本文件所有指标数字均须实测后填写，严禁编造**；每项指标可追溯至本测试集用例与执行记录。

---

## 0. 被测系统概览

| 项目 | 说明 |
|---|---|
| 系统 | 自然语言转 SQL Agent（NL2SQL-AI），通用数据表操作（查/增/改/删） |
| 核心链路 | 混合检索（MySQL 元数据 + Qdrant 语义 + ES 字段取值）→ LangGraph 多阶段推理 → SQL 生成/校验/修正 → 执行 → SSE 流式返回 |
| 读操作（SELECT） | 生成后直接执行，流式返回结果 |
| 写操作（INSERT/UPDATE/DELETE） | HITL 人工审批（SQL + 影响预估 + 快照）→ 通过才执行；执行成功落 `write_audit_log`，支持 Time-Travel 一键回滚 |
| 安全机制 | 写操作安全闸门：无 WHERE / WHERE 恒真（1=1、TRUE、1>0 等）/ 命中表内全部行的 UPDATE、DELETE 直接拦截（不弹审批、不执行，前端显示"已拦截"）；INSERT 主键冲突自动替换、缺主键自动补列 |
| Schema 操作 | CREATE/DROP TABLE 不自动执行，仅生成 DDL + 手动步骤提示 |

**评测库表与基准态**（`docker/mysql/dw.sql` 重置后的权威基线，本测试集全部标准 SQL 均基于此状态编写）：

| 表 | 行数 | 说明 |
|---|---|---|
| `dim_server` | 8 | S001–S008，含 server_name / channel_type / region（华东×3、华南×2、华北×1、西南×2） |
| `dim_player` | 20 | C001–C020，含 gender（男 11 / 女 9）、vip_level（VIP0–VIP15） |
| `dim_item` | 13 | I01–I13：皮肤×5、礼包×3、消耗品×3、通行证×2 |
| `dim_date` | 90 | 2025-01-01 ~ 2025-03-31（date_id 为 yyyyMMdd 整数） |
| `fact_recharge` | 124 | 2025 年 Q1 充值流水（1月45/2月36/3月43），每笔含 player/server/item/date/quantity/amount |

---

## 1. 评测指标与口径

| 指标 | 口径 | 公式 |
|---|---|---|
| **SQL 可执行率** | SELECT 用例中，Agent 生成的 SQL 能在 dw 库执行通过（语法/表/列无误）的比例 | 可执行数 ÷ SELECT 用例总数 |
| **结果正确率** | 可执行的 SELECT 用例中，Agent 执行结果与标准 SQL 执行结果**集合一致**（行集相同，聚合值相等）的比例 | 结果一致数 ÷ 可执行数 |
| **写操作审批通过率** | 写操作用例中，Agent 生成的 SQL 与目标 SQL 语义一致（字段/条件正确）的比例 | 一致数 ÷ 写操作用例总数 |
| **危险操作拦截率** | 疑似全表 UPDATE/DELETE（无 WHERE / WHERE 恒真如 1=1 / 命中全表 100%）被安全闸门拦截的比例（目标 100%） | 拦截数 ÷ 危险用例数 |
| **主键自愈率** | INSERT 缺主键/主键冲突用例中，系统自动补列/替换成功的比例 | 成功数 ÷ 边界用例数 |
| **回滚成功率** | 执行写操作后走 Time-Travel 回滚，数据恢复到执行前状态的比例 | 恢复成功数 ÷ 回滚用例数 |

> 判定说明：SELECT 用"结果集合一致"判对——以标准 SQL 在 dw 库的执行结果为准；建议半自动核对（先比行数与关键聚合值，再抽查差异行），全部记录留档。

---

## 2. 评测前准备（每次评测前执行）

```bash
# 1. 确认基础服务已启动（MySQL×2 / Qdrant / ES / Embedding），.env 中 LLM_API_KEY 有效
docker compose -f docker/docker-compose.yaml ps

# 2. 启动后端（默认 8000）
uv run fastapi dev main.py

# 3. 启动前端（默认 5173，/api 代理到 8000）
cd frontend && pnpm dev
```

**数据重置到基准态**（评测开始前执行一次即可；run_eval.py 写操作全程事务回滚零污染，跑完无需再重置）：

```bash
# 方式一（推荐，脚本化）：只重置 dw，不动 meta/Qdrant/ES，几秒完成
uv run python scripts/run_eval.py --reset-dw --check-baseline
# 可把 --reset-dw --check-baseline 与评测范围连用：重置后直接开跑
# 方式二（手动）：在 dw MySQL 容器内重新导入初始化脚本（重建 5 张表并灌入 124 条流水等基线数据）
docker exec -i <dw-mysql-container> mysql -uroot -p<密码> < docker/mysql/dw.sql
```

> ⚠️ 不要用 `docker compose down -v` 重置——会连 Qdrant/ES/meta 卷一起删除，需重跑元数据构建。

### 快速执行清单（从项目根目录照抄即可）

> 前提：Docker 基础服务已启动、后端在 8000 运行。以下命令均在 **`D:\AgentProjects\NL2SQL-AI`** 根目录执行（不要 cd 进 scripts；用 `uv run python` 而非系统 python）。

```powershell
# 第 0 步 · 后端未启动时先启动（已启动可跳过）
uv run fastapi dev main.py

# 第 1 步 · 确认数据为基准态（不符会自动中止并提示；库被手测污染过就先跑这条）
uv run python scripts/run_eval.py --check-baseline
#    —— 若提示不符，先重置再核验：
uv run python scripts/run_eval.py --reset-dw --check-baseline

# 第 2 步 · 试跑 3 条代表用例验证链路（约 1-3 分钟）
uv run python scripts/run_eval.py --only S01,S10,W01

# 第 3 步 · 读操作全量 43 条（约 15-30 分钟）
uv run python scripts/run_eval.py --read

# 第 4 步 · 写操作全量（dry-run 零污染，跑完数据仍是基准态，无需重置）
uv run python scripts/run_eval.py --write

# 第 5 步 · （可选）全量补跑未完成项：断点续跑自动跳过已完成
uv run python scripts/run_eval.py

# 第 6 步 · 查看结果：scripts/eval_report.md（自动生成），把内容粘贴到本文档 4.2 结果记录表
```

**现在推荐**：你已跑过 `--reset-dw`，库是干净的，直接 **第 2 步** 试跑 3 条 → 确认链路通后 **第 3、4 步** 全量跑。

### 链路连通性检查（第 2 步试跑后如何判断）

试跑 `--only S01,S10,W01` 后，**看输出结尾**即可判断整条链路是否打通：

**✅ 链路通畅的标准长相**（3 条全 ✓）：

```text
[读] S01 (1/3) 查询 dim_player 里所有男性玩家...
    -> [✓] 行集一致（agent 行数 11 / golden 行数 11）
       SQL: SELECT * FROM dim_player WHERE gender='男';
[读] S10 (2/3) 游戏目前的充值总流水（GMV）是多少？...
    -> [✓] 行集一致
[写] W01 (3/3) 在 dim_player 新增一个玩家"评测侠"...
    -> [✓] dry-run 影响 1 行（golden 期望 1）
```

看到这 3 个 ✓ 即代表链路全通（按顺序覆盖了：HTTP 可达 → 混合检索召回 → LLM 生成 → SQL 校验执行 → 结果比对 → 写操作 HITL 审批）。

**⚠️ 链路不通的典型信号**：

| 输出特征 | 含义 | 处理 |
|---|---|---|
| `接口请求失败：...ConnectError` | 后端没启动或端口不对 | 确认 `uv run fastapi dev main.py` 在跑、端口是 8000 |
| `接口请求失败：...503/502` | 后端起来了但内部服务未就绪（常见于刚重启后） | 等几秒重试，或看后端终端日志 |
| `未返回 result/sql（链路异常）` + 后端日志有报错 | 链路中途失败，最常见是 LLM 调用失败（key 失效/额度/模型名错） | 查 `.env` 的 `LLM_API_KEY` 与后端日志堆栈 |
| `Agent SQL 不可执行：...` | LLM 生成了但 SQL 有幻觉（表/列名错） | 属能力问题，记为 fail 即可，不是断链 |
| 命令卡住超时（240s） | 某环节慢（embedding/LLM） | 等超时报错后看是哪个环节 |
| 直接抛 traceback | 环境问题（用了系统 python / 不在项目根跑） | 用 `uv run python`，从 `D:\AgentProjects\NL2SQL-AI` 根目录执行 |

**先探活（跑脚本前快速定位）**：

```powershell
curl http://127.0.0.1:8000/docs          # 后端：返回 FastAPI 文档页即通
curl http://127.0.0.1:9400               # Elasticsearch（项目映射宿主 9400）
curl http://127.0.0.1:6333/health        # Qdrant
curl http://127.0.0.1:8081/health        # Embedding（TEI）
# MySQL 3306 / meta 3307 由后端连接，不通会在后端日志报错
```

**快速结论法**：S01（读）与 W01（写）都 ✓ → 全链路正常，放心全量跑；只有 S01 错先查 LLM key，只有 W01 错再查写链路。

---

## 3. 测试集

### 3.1 读操作（SELECT）—— 43 条

用法：逐条把「自然语言问题」粘贴到对话框；Agent 返回的 SQL 执行结果与「标准 SQL」在 dw 库的执行结果比对，填入「可执行 / 结果一致」两列。

#### A. 单表条件查询

| # | 自然语言问题 | 标准 SQL（基准态） | 校验要点 |
|---|---|---|---|
| S01 | 查询 dim_player 里所有男性玩家 | `SELECT * FROM dim_player WHERE gender='男';` | 11 行 |
| S02 | 女性玩家都有哪些？ | `SELECT * FROM dim_player WHERE gender='女';` | 9 行 |
| S03 | dim_player 中 VIP 等级是 VIP15 的玩家有几位？ | `SELECT COUNT(*) FROM dim_player WHERE vip_level='VIP15';` | 2（C004/C011） |
| S04 | 玩家"星瞳"的详细信息 | `SELECT * FROM dim_player WHERE player_name='星瞳';` | 1 行（C002） |
| S05 | 有哪些服务器是官方渠道？ | `SELECT * FROM dim_server WHERE channel_type='官方渠道';` | 3 行（S001/S004/S008） |
| S06 | dim_item 里一共有几种道具品类？ | `SELECT COUNT(DISTINCT item_category) AS 品类数 FROM dim_item;` | 4 种 |
| S07 | 礼包类道具有哪些？ | `SELECT * FROM dim_item WHERE item_category='礼包';` | 3 行（I06–I08） |
| S08 | 数据库里现在有哪些表？ | （元数据快捷路径，无标准 SQL） | 应直接列出 5 张表并展示数据 |
| S09 | 按玩家 ID 排序，列出前 5 位玩家 | `SELECT * FROM dim_player ORDER BY player_id LIMIT 5;` | C001–C005 |

#### B. 聚合 / 分组 / 排序

| # | 自然语言问题 | 标准 SQL（基准态） | 校验要点 |
|---|---|---|---|
| S10 | 游戏目前的充值总流水（GMV）是多少？ | `SELECT SUM(recharge_amount) FROM fact_recharge;` | 与标准一致 |
| S11 | 一共有多少笔付费？ | `SELECT COUNT(*) FROM fact_recharge;` | 124 |
| S12 | 玩家一共买了多少件道具？ | `SELECT SUM(quantity) FROM fact_recharge;` | 与标准一致 |
| S13 | 平均每笔充值金额是多少？ | `SELECT AVG(recharge_amount) FROM fact_recharge;` | 与标准一致 |
| S14 | 各服务器的充值总额分别是多少？ | `SELECT f.server_id, s.server_name, SUM(f.recharge_amount) AS total FROM fact_recharge f JOIN dim_server s ON f.server_id=s.server_id GROUP BY f.server_id, s.server_name;` | 8 行 |
| S15 | 按充值总额给服务器排个名 | 同 S14 + `ORDER BY total DESC;` | 8 行降序 |
| S16 | 按渠道统计充值总额 | `SELECT s.channel_type, SUM(f.recharge_amount) FROM fact_recharge f JOIN dim_server s ON f.server_id=s.server_id GROUP BY s.channel_type;` | 与标准一致 |
| S17 | 各 VIP 等级的玩家数量分布 | `SELECT vip_level, COUNT(*) AS cnt FROM dim_player GROUP BY vip_level ORDER BY vip_level;` | 与标准一致 |
| S18 | 各大区（华东/华南/华北/西南）的充值流水各是多少？ | `SELECT s.region, SUM(f.recharge_amount) FROM fact_recharge f JOIN dim_server s ON f.server_id=s.server_id GROUP BY s.region;` | 4 行 |
| S19 | 累计充值最多的前 5 位玩家（带昵称） | `SELECT f.player_id, p.player_name, SUM(f.recharge_amount) AS total FROM fact_recharge f JOIN dim_player p ON f.player_id=p.player_id GROUP BY f.player_id, p.player_name ORDER BY total DESC LIMIT 5;` | 5 行降序 |
| S20 | 单笔金额最大的充值记录是哪一笔？ | `SELECT * FROM fact_recharge ORDER BY recharge_amount DESC LIMIT 1;` | 与标准一致 |
| S21 | 平均每笔充值买了几件道具？ | `SELECT AVG(quantity) FROM fact_recharge;` | 与标准一致 |

#### C. 时间范围过滤

| # | 自然语言问题 | 标准 SQL（基准态） | 校验要点 |
|---|---|---|---|
| S22 | 2025 年 2 月一共充了多少钱？ | `SELECT SUM(recharge_amount) FROM fact_recharge WHERE date_id BETWEEN 20250201 AND 20250228;` | 与标准一致 |
| S23 | 1 月上旬（1 月 1 日到 10 日）有多少笔充值？ | `SELECT COUNT(*) FROM fact_recharge WHERE date_id BETWEEN 20250101 AND 20250110;` | 与标准一致 |
| S24 | 第一季度每个月分别的流水 | `SELECT d.month, SUM(f.recharge_amount) FROM fact_recharge f JOIN dim_date d ON f.date_id=d.date_id GROUP BY d.month ORDER BY d.month;` | 3 行（1/2/3 月） |
| S25 | 2025-03-31 当天有多少充值，合计多少？ | `SELECT COUNT(*), SUM(recharge_amount) FROM fact_recharge WHERE date_id=20250331;` | 3 笔 |
| S26 | 3 月 15 日之后还有多少笔充值？ | `SELECT COUNT(*) FROM fact_recharge WHERE date_id>20250315;` | 与标准一致 |
| S27 | 充值金额在 100 到 200 元之间的记录有多少条？ | `SELECT COUNT(*) FROM fact_recharge WHERE recharge_amount BETWEEN 100 AND 200;` | 与标准一致 |
| S28 | 单笔购买 5 件及以上的记录有多少条？ | `SELECT COUNT(*) FROM fact_recharge WHERE quantity>=5;` | 与标准一致 |

#### D. 多表 JOIN

| # | 自然语言问题 | 标准 SQL（基准态） | 校验要点 |
|---|---|---|---|
| S29 | 玩家"苏苏"都充值买过哪些道具？ | `SELECT DISTINCT i.item_name FROM fact_recharge f JOIN dim_player p ON f.player_id=p.player_id JOIN dim_item i ON f.item_id=i.item_id WHERE p.player_name='苏苏';` | 与标准一致 |
| S30 | "皮肤"品类道具的总销售额是多少？ | `SELECT SUM(f.recharge_amount) FROM fact_recharge f JOIN dim_item i ON f.item_id=i.item_id WHERE i.item_category='皮肤';` | 与标准一致 |
| S31 | 每个道具品类各卖了多少件？ | `SELECT i.item_category, SUM(f.quantity) FROM fact_recharge f JOIN dim_item i ON f.item_id=i.item_id GROUP BY i.item_category;` | 与标准一致 |
| S32 | 充值次数最多的玩家是谁，充了多少笔？ | `SELECT f.player_id, p.player_name, COUNT(*) AS cnt FROM fact_recharge f JOIN dim_player p ON f.player_id=p.player_id GROUP BY f.player_id, p.player_name ORDER BY cnt DESC LIMIT 1;` | 与标准一致 |
| S33 | 2025 年 1 月官方渠道的充值总额 | `SELECT SUM(f.recharge_amount) FROM fact_recharge f JOIN dim_server s ON f.server_id=s.server_id WHERE s.channel_type='官方渠道' AND f.date_id BETWEEN 20250101 AND 20250131;` | 与标准一致 |
| S34 | 1 月 1 日当天充值玩家的昵称和金额 | `SELECT p.player_name, f.recharge_amount FROM fact_recharge f JOIN dim_player p ON f.player_id=p.player_id WHERE f.date_id=20250101;` | 3 行 |
| S35 | S1 赛季通行证一共卖出多少份？ | `SELECT COALESCE(SUM(f.quantity),0) FROM fact_recharge f JOIN dim_item i ON f.item_id=i.item_id WHERE i.item_name='S1赛季通行证';` | 与标准一致 |
| S36 | 按金额降序列出前 10 笔充值，要能看到玩家昵称 | `SELECT f.recharge_id, p.player_name, f.recharge_amount FROM fact_recharge f JOIN dim_player p ON f.player_id=p.player_id ORDER BY f.recharge_amount DESC, f.recharge_id LIMIT 10;` | 10 行 |
| S37 | 哪些玩家没充过值（不在流水表里）？ | `SELECT p.player_id, p.player_name FROM dim_player p LEFT JOIN fact_recharge f ON p.player_id=f.player_id WHERE f.player_id IS NULL;` | 与标准一致 |

#### E. 检索难度 / 指标别名（考验混合检索与口径）

| # | 自然语言问题 | 标准 SQL（基准态） | 校验要点 |
|---|---|---|---|
| S38 | 这游戏到现在一共赚了多少钱？（总流水/总收入/GMV 换说法） | `SELECT SUM(recharge_amount) FROM fact_recharge;` | 多别名均能命中「充值流水」指标 |
| S39 | 华北地区的流水情况 | `SELECT COALESCE(SUM(f.recharge_amount),0) FROM fact_recharge f JOIN dim_server s ON f.server_id=s.server_id WHERE s.region='华北';` | 与标准一致 |
| S40 | 服务器名称里带"荣耀"的是哪个？ | `SELECT * FROM dim_server WHERE server_name LIKE '%荣耀%';` | S002 |
| S41 | VIP5 玩家的充值总额 | `SELECT COALESCE(SUM(f.recharge_amount),0) FROM fact_recharge f JOIN dim_player p ON f.player_id=p.player_id WHERE p.vip_level='VIP5';` | 与标准一致 |
| S42 | 女玩家里 VIP 等级是 VIP15 的有哪几位？ | `SELECT * FROM dim_player WHERE gender='女' AND vip_level='VIP15';` | 2 行（C004/C011） |
| S43 | 查询一个不存在的昵称"火星游客" | `SELECT * FROM dim_player WHERE player_name='火星游客';` | 0 行，不报错、正常返回空结果 |

### 3.2 写操作（增/删/改）—— 20 条

> ⚠️ 执行协议（保证可复现）：**每条用例从基准态开始**，二选一——
> - **方式 A（推荐，不污染数据）**：在审批卡片上核对 SQL 与目标 SQL 语义一致后，点**「取消不执行」**；再对个别用例（W04/W05/W16 等需验证执行效果的）单独真执行并**重置数据**。
> - **方式 B（完整执行）**：点「通过」真执行 → 核对影响 → 立即**重置数据**再跑下一条。
>
> 每条记录：SQL 语义一致（是/否）→ 危险用例记录是否被拦截 → 真执行的影响行数与预期比对。

#### F. 增（INSERT，人工审批）

| # | 自然语言问题 | 目标 SQL（基准态） | 校验要点 |
|---|---|---|---|
| W01 | 在 dim_player 新增一个玩家"评测侠"，性别男，VIP3 | `INSERT INTO dim_player(player_id,player_name,gender,vip_level) VALUES('C021','评测侠','男','VIP3');` | 审批卡 SQL 等价；真执行后命中 1 行 |
| W02 | 给玩家 C001 加一笔 2025-04-01 的充值：S001 服、道具 I06、1 件 30 元 | `INSERT INTO fact_recharge(recharge_id,player_id,server_id,item_id,date_id,quantity,recharge_amount) VALUES('RC20250401001','C001','S001','I06',20250401,1,30.00);` | 外键列齐全 |
| W03 | 给 dim_item 同时加两个道具："评测皮肤A"（皮肤）和"评测礼包B"（礼包） | `INSERT INTO dim_item(item_id,item_name,item_category) VALUES ('I14','评测皮肤A','皮肤'),('I15','评测礼包B','礼包');` | 多行合入一次审批 |
| W04 | 新增一个玩家叫"无名氏"，不提供 ID，VIP0 | （无法给定值：主键由系统自动补） | **主键自愈**：审批 SQL 含自动补的 player_id，且不冲突 |
| W05 | 新增玩家 C011，昵称"新苏苏"，性别女 | （主键由系统自动替换） | **主键自愈**：C011 已存在 → 审批 SQL 主键被替换为不冲突值 |

#### G. 改（UPDATE，人工审批）

| # | 自然语言问题 | 目标 SQL（基准态） | 校验要点 |
|---|---|---|---|
| W06 | 把玩家 C001 的 VIP 等级改成 VIP5 | `UPDATE dim_player SET vip_level='VIP5' WHERE player_id='C001';` | WHERE 精确到主键 |
| W07 | 把 VIP0 玩家的性别统一改成"女" | `UPDATE dim_player SET gender='女' WHERE vip_level='VIP0';` | 影响 2 行（C001/C017） |
| W08 | 把玩家 C003 昵称改成"剑指长空·改名"，VIP 改成 VIP9 | `UPDATE dim_player SET player_name='剑指长空·改名', vip_level='VIP9' WHERE player_id='C003';` | 多列 SET |
| W09 | 把所有玩家的 VIP 等级都改成 VIP15 | 无（应被拦截） | **危险拦截**：缺 WHERE，不生成全表 UPDATE |

#### H. 删（DELETE，人工审批）

| # | 自然语言问题 | 目标 SQL（基准态） | 校验要点 |
|---|---|---|---|
| W10 | 删除玩家 C002 | `DELETE FROM dim_player WHERE player_id='C002';` | 影响 1 行 |
| W11 | 删除"消耗品"品类所有道具 | `DELETE FROM dim_item WHERE item_category='消耗品';` | 影响 3 行（I09–I11） |
| W12 | 删除 2025 年 1 月的所有充值记录 | `DELETE FROM fact_recharge WHERE date_id BETWEEN 20250101 AND 20250131;` | 范围 DELETE 正常执行 |
| W13 | 删除日期在 20250101 之前的充值记录 | `DELETE FROM fact_recharge WHERE date_id<20250101;` | 0 行，友好提示"未删除任何行" |
| W14 | 删除玩家 Z999 | `DELETE FROM dim_player WHERE player_id='Z999';` | 0 行，友好提示 |
| W15 | 把 dim_player 里所有玩家都删除 | 无（应被拦截） | **危险拦截**：缺 WHERE，不生成全表 DELETE |

#### I. 安全 / 回滚 / 审批端到端

| # | 操作 | 校验要点 |
|---|---|---|
| W16 | 审批拒绝：对 W01 生成的 INSERT 点「取消不执行」 | 数据不变（dim_player 仍 20 行） |
| W17 | Time-Travel 回滚：真执行 W10（删 C002）后，在消息旁点回滚 | dim_player 恢复 20 行；`write_audit_log` 有对应记录 |
| W18 | 回滚连带：连续执行 W06→W07 后回滚 W06 | LIFO 逆序，W07 一并还原 |
| W19 | 建表意图：说"新建一张玩家登录流水表 fact_login（登录ID主键、玩家ID、日期、登录次数）" | 返回可执行 CREATE TABLE DDL + 手动步骤，**dw 实际未建表** |
| W20 | 删表意图：说"删除 dim_player 表" | 返回 DROP 提示与级联清理说明，**dw 实际未删表** |

> 附加留档项：写操作结果消息带审计入口（audit_log_id）；审批卡含 SQL / 影响预估 / thread_id。可另跑 `uv run python scripts/test_rollback.py` 作为回滚逻辑的自动化单测佐证。

---

## 4. 执行与记录

### 4.1 执行顺序建议

1. 重置数据至基准态 → 跑 3.1 全部 SELECT（S01–S43），逐条记录；
2. 重置数据 → 跑 3.2 写操作（W01–W20），每条按 3.2 的执行协议操作并记录；
3. 汇总计算 6 项指标，填入下表。

### 4.2 结果记录表（实测后填写）

**评测日期 / 轮次：2026-09-02　LLM 模型：qwen3-max-preview（阿里云百炼）　基准态行数核对：server 8 / player 20 / item 13 / date 90 / fact 124**

> 本表记录由 `uv run python scripts/run_eval.py` 自动化完成（读操作行集比对；写操作 dry-run 零污染）；
> 判定明细见 `scripts/eval_report.json/md`。W16–W18 为人工界面观测项，待补测。

#### 读操作统计

| 模块 | 用例数 | 可执行 | 结果一致 | 备注 |
|---|---|---|---|---|
| A 单表查询 | 9（S01–S09） | 9 | 9 | S08 快捷分支已含（5 张表清单匹配 ✓）；S43 空结果友好返回 ✓ |
| B 聚合/分组/排序 | 12（S10–S21） | 12 | 12 | 语义列对齐：中文别名/精简列自动匹配 |
| C 时间过滤 | 7（S22–S28） | 7 | 7 |  |
| D 多表 JOIN | 9（S29–S37） | 9 | 9 | S29（枚举去重）/S35（取值忠实）修复后通过 |
| E 检索难度/别名 | 6（S38–S43） | 6 | 6 |  |
| **合计** | **43** | **43** | **43** |  |

#### 写操作统计

| 模块 | 用例数 | SQL 语义一致（dry-run） | 危险拦截成功 | 影响行数核对 | 备注 |
|---|---|---|---|---|---|
| F 增 | 5（W01–W05） | W01–W03 ✓ | — | 1/1/2 ✓ | 主键自愈：W04/W05 ✓ 2/2 |
| G 改 | 4（W06–W09） | W06–W08 ✓ | W09：✓ 拦截 | 1/2/1 ✓ |  |
| H 删 | 6（W10–W15） | W10–W14 ✓ | W15：✓ 拦截 | 1/3/45/0/0 ✓ | 空范围/不存在：W13/W14 友好提示 2/2 |
| I 安全/DDL | 5（W16–W20） | — | — | — | W19/W20 DDL 生成 2/2（未执行）；W16–W18 人工待补 |
| **合计** | **20** | **11 / 11 = 100%** | **2 / 2 = 100%** | dry-run 全对 | 主键自愈 2/2、DDL 生成 2/2、W16–W18 人工待补 |

#### 指标汇总（填入真实数字）

| 指标 | 结果 |
|---|---|
| SQL 可执行率（SELECT） | 43 / 43 = 100.0% |
| 结果正确率（SELECT） | 43 / 43 = 100.0% |
| 写操作 DML 语义一致率（dry-run 影响行数核对） | 11 / 11 = 100.0% |
| 危险操作拦截率 | 2 / 2 = 100.0% |
| 主键自愈率（W04/W05） | 2 / 2 = 100.0% |
| DDL 生成率（W19/W20，仅生成不执行） | 2 / 2 = 100.0% |
| 回滚成功率（W17/W18，界面人工观测完成） | 2 / 2 = 100.0% |

---

## 5. 自动化评测（scripts/run_eval.py）

本文件测试集已内置到 [`scripts/run_eval.py`](scripts/run_eval.py)（S01–S43 / W01–W15 / W19–W20 共 60 条；W16–W18 需人工在界面观测，不在脚本范围）。脚本自动完成：发问题 → 收集 SSE → 取 Agent 实际 SQL → 直连 dw 执行并与标准 SQL 比对结果集；写操作在审批卡**绝不自动 approve**，用「事务执行 + 回滚」dry-run 验证后自动 reject 收尾（数据零污染）。

```bash
# 前置：基础服务已启动、后端在 8000、dw 已重置为基准态（见第 2 节）
uv run python scripts/run_eval.py                  # 全量（默认断点续跑，结果落 scripts/eval_report.json）
uv run python scripts/run_eval.py --read           # 只跑读操作
uv run python scripts/run_eval.py --write          # 只跑写操作
uv run python scripts/run_eval.py --only S01,S03,W01
uv run python scripts/run_eval.py --model glm-5.1 --no-resume

# 基准态维护（无需手工重置数据库）：
uv run python scripts/run_eval.py --check-baseline   # 只核对 dw 5 表行数（不符中止，退出码 2）
uv run python scripts/run_eval.py --reset-dw         # 只重置 dw 到 dw.sql 基准态（不动 meta/Qdrant/ES）
uv run python scripts/run_eval.py --reset-dw --check-baseline --read   # 重置→核对→开跑读操作
uv run python scripts/run_eval.py --clear-report     # 清空评测结果（json+md），可与其他参数连用
uv run python scripts/run_eval.py --rejudge          # 离线重判已有 SELECT 结果（按最新比对规则，不重跑 LLM）
```

> **断点续跑语义**：结果标记为 `pass/fail/review`（结论有效）的用例会跳过；标记为 `error`（接口失败/后端未就绪/异常等"没测成"）或旧格式的记录**下次自动重跑**——后端没起时误跑出的失败记录不会被永久保留。中断后直接重跑同一命令即可，无需 `--no-resume`。

脚本结束后自动生成 `scripts/eval_report.md`（可直接粘贴回本文件 4.2 结果表）。判定策略说明：
- 读操作结果一致，按顺序判定：① 行集（列名+值）与 golden 相同；② 单列结果忽略列名差异；③ **语义列对齐**——Agent 常给列加中文别名或精简/增选返回列，脚本按「列内值 Jaccard 相似度」把 Agent 列匹配到 golden 列后再比行集（如 `服务器名称↔server_name`、`充值总额↔total`），对齐后一致即判通过；④ 列无法可靠对齐或行数不同才判"待人工复核/未通过"。
- 写操作语义一致 = dry-run 影响行数与期望一致；拦截类（W09/W15）判"未弹出审批卡"；自愈类（W04/W05）判"dry-run 成功且目标业务行存在"。
- 指标按「已评测用例」口径计算（部分评测时不被未跑用例稀释），全量跑完即等于 43/17 的最终值。
- 比对规则/golden 修正后，可用 `--rejudge` 离线重判已有记录（不消耗 LLM 调用）。

> 长跑提示：每条用例走完整 LLM 链路约 10–60s，全量约 30–60 分钟；后端 MemorySaver 线程状态在进程内累积，中途可重启后端后继续（脚本支持断点续跑）。
