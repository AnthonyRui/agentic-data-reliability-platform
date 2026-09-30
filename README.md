# Agentic Data Reliability Platform

帮助发现数据异常、调查原因，并在隔离测试环境验证修复建议的数据可靠性平台。

## 当前状态

正常数据管道已接通：模拟数据、PostgreSQL、dbt 和 Dagster 可通过一条命令运行并保存历史。[9 月 30 日自动检查](https://github.com/AnthonyRui/agentic-data-reliability-platform/actions/runs/36763722041) 已通过 Windows/Linux 检查、真实数据库两轮运行和历史验收。完整产品、定时数据调度与 AI 功能仍待开发，没有 AI 评估成绩。

本项目使用模拟电商数据。未来演示流程为：销售额异常 → 自动调查 → 展示根因与证据 → 提出修复 → 沙箱验证 → 人工批准后导出代码变更。

## 三个版本

| 版本 | 目标 | 计划阶段 |
| --- | --- | --- |
| V1 | 发现数据问题，提供基础调查与页面 | 第 1–4 周 |
| V2 | 完成 AI 调查、真实 MCP/A2A 调用及沙箱修复闭环 | 第 5–9 周 |
| V3 | 十类故障、30–50 个真实评估案例和完整交付 | 第 10–12 周 |

计划周期：2026-09-23 至 2026-12-23。日期是目标，完成状态以实际测试和验收为准。

## 开发方式

每周一、三、五达拉斯当地时间下午 2 点开始定时推进开发。测试通过后 commit 和 push；上传时间取决于本次工作量。

- `develop`：日常开发分支。
- `main`：经过验收的稳定版本；当前仅包含初始化说明。
- [开发进度](docs/PROGRESS.md)：已完成、未完成、验证记录和下一步。

## 技术方向

计划采用 PostgreSQL、dbt、Dagster、FastAPI、React，以及 MCP 工具接口和两个独立 A2A 助手服务。实际版本与启动步骤会在实现和验证后记录。

调查使用只读权限；修复先进入隔离环境验证；不自动修改生产数据。模型不可用时，基础数据管道和质量检查仍应能运行。

## 如何运行

### 只生成数据

需要 Python 3.11，不需要额外 Python 库或 AI 密钥。在项目根目录执行：

```powershell
py -3.11 -m data.generator.generate
py -3.11 -m unittest discover -s tests -p test_generator.py -v
```

macOS/Linux 将 `py -3.11` 换成 `python3.11`。Windows 上不要直接使用旧版 `python`；本次环境中的默认版本是 3.6.5。

输出位于 `data/generated/`，包含三个原始数据 CSV、每日正确值对照表和带 SHA256 校验值的 manifest。默认覆盖 2025-01-01 至 2026-06-30，共 546 天。相同配置和种子产生相同文件，已有输出目录不会被覆盖。再次试验可指定 `--output .local/another-dataset --seed 43`；数据库默认仍读取 `data/generated/`。

收入定义：按 UTC 日汇总状态为 `completed` 的订单金额，币种为 USD，取消订单不计入收入。金额以整数分计算并导出两位小数，避免浮点金额误差。日期固定，当前不以真实今天做新鲜度判断。

### 启动基础数据库

需要可用的 Docker Engine 和 Compose v2。先生成上面的数据，将 `.env.example` 复制为 `.env` 并填写本地数据库密码，然后执行：

```text
docker compose config --quiet
docker compose up -d --wait --wait-timeout 150
python scripts/verify_database.py
```

最后一行在 Windows 上使用 `py -3.11 scripts/verify_database.py`。验证脚本比较数据库实际行数、每日营收、关联完整性、重复记录以及只读角色权限。数据库只监听 `127.0.0.1:5433`，库名 `reliability`，管理员用户名 `reliability_admin`。管理员仅用于本地基础设施；未来调查 Agent 不得使用该账号。

`docker compose stop` 停止数据库并保留数据，`docker compose start --wait` 恢复。CSV 仅在全新的数据卷首次初始化时导入；更换 CSV 不会重新导入已有数据库。后续提供独立、受控的故障恢复功能，目前不要用重新启动代替重新导入。

本机未安装 Docker；本地只验证了 Python 路径。2026-09-23 的 [Foundation checks](https://github.com/AnthonyRui/agentic-data-reliability-platform/actions/runs/35908214284) 已在 GitHub Linux 环境通过真实数据库启动及比对，Windows/Linux Python 检查也通过。若导入中断，健康检查不会误报就绪；请先查看 `docker compose logs postgres`，不要忽略失败继续开发。

## 基础架构

固定配置与随机种子 → 模拟 CSV 和正确值对照 → PostgreSQL 原始表 → dbt staging → 销售明细 → 每日营收、地区营收和总览指标 → 独立 Python 对照验证。

Dagster 已串联上述步骤；后续加入定时数据调度、质量监控、事件页面和 Agent 调查。原始订单表允许未来注入重复或缺失数据；正常基线通过测试检查完整性。只读权限组尚未创建 Agent 登录凭据。

### 运行数据清洗与指标

在 Python 3.11 虚拟环境安装锁定依赖，并先启动上面的数据库。下列 `python` 应指向该虚拟环境；Windows 可用 `.venv/Scripts/python.exe`，macOS/Linux 可用 `.venv/bin/python`：

```text
python -m pip install -r requirements-pipeline.txt
python -m scripts.run_dbt parse
python -m scripts.run_dbt build
python -m scripts.verify_models
```

运行器读取根目录 `.env` 的 `POSTGRES_PASSWORD`（或环境变量 `DBT_ENV_SECRET_POSTGRES_PASSWORD`）。密码不写入 profiles.yml，日志使用 dbt 的 secret 环境变量脱敏。`parse` 只检查配置，`build` 才真正运行模型和测试。当前依赖锁定针对 Python 3.11。

首次启动的新数据库会自动创建转换权限组。若已有上一次版本的数据卷，先执行以下可重复的升级命令（保留已有订单，不重置数据卷）：

```text
docker compose up -d --wait --wait-timeout 150
docker compose exec -T postgres psql -U reliability_admin -d reliability -f /docker-entrypoint-initdb.d/002_transform.sql
```

七个模型写入 `analytics` schema：`stg_orders`、`stg_customers`、`stg_products`、`fact_sales`、`daily_revenue`、`regional_revenue`、`executive_metrics`。dbt 连接后切换为 `reliability_transform`，该权限组只能读取原始数据并在 analytics 中构建；这仍不是生产环境的独立登录隔离。

订单数包括取消订单，收入和客单价只计算完成订单；没有完成订单时收入为 0、客单价为空。总览客户数按整个期间去重，不累加每日客户数。当前只接受 USD，不擅自换算其他币种。缺失关联保留为 NULL 并使测试失败，不静默丢掉订单。

dbt 测试检查唯一值、必填值、关联、币种、金额、汇总一致性，并包含取消订单、四舍五入和 UTC 跨日边界的固定案例。独立验证器从原始 CSV 重新计算三类指标并与真实数据库逐行比较；不使用待测 SQL 自己证明自己正确。

## 开发检查

```text
python -m pip install -r requirements-dev.txt -r requirements-pipeline.txt
python -m ruff check .
python -m ruff format --check .
python -m unittest discover -s tests -v
```

建议使用独立虚拟环境。Python 格式工具固定版本；PostgreSQL 官方 17 Bookworm 镜像及 GitHub Actions 固定到摘要/提交，便于复现。升级须经过同样检查。

## 已知限制

目前包含基础数据、数据库、dbt 转换和 Dagster 手动任务，不包含网页、故障注入、AI 调查和修复。生成器使用一个 USD 订单对应一个商品的简化模型，包含趋势、周末波动和年度季节性。只读权限组还不是完整 SQL 安全防护。

首次初始化失败或更换数据集暂需人工处理。不包含真实客户数据、账户密钥或虚构测试结果。

参考：[PostgreSQL COPY](https://www.postgresql.org/docs/17/sql-copy.html)、[官方 PostgreSQL 镜像](https://hub.docker.com/_/postgres)、[Compose 启动和健康检查](https://docs.docker.com/compose/how-tos/startup-order/)。

### 一条命令运行完整管道

前提：Python 3.11 虚拟环境已安装 `requirements-pipeline.txt`，Docker 正常运行，根目录 `.env` 已填写本地密码。在项目根目录运行：

```text
python -m scripts.run_pipeline
```

命令会生成或校验模拟数据、启动数据库、升级转换权限，然后通过 Dagster 检查原始数据、执行 dbt 和比对最终指标。已有 CSV 不会被覆盖；数据库保留已有数据。原始数据验证失败时，下游计算不会执行，进程返回失败。启动前失败与 Dagster 任务失败分别记录，不假装成功。

运行记录存储在 `.local/dagster/`，每次任务另有 `.local/runs/` JSON 摘要，均不上传仓库。`.local/pipeline.lock` 防止命令并发；意外退出遗留锁时，必须确认旧进程已经结束再移除。启动阶段的错误发生在 Dagster 任务创建之前。

`python -m scripts.run_pipeline --check` 只解析 dbt 和验证 Dagster 定义，不需要 Docker，但仍需要配置密码变量。它不代表数据库验收通过。

查看本地运行历史（PowerShell，已激活虚拟环境）：

```powershell
$env:DAGSTER_HOME = Join-Path (Get-Location) '.local/dagster'
dagster dev -m dagster_project.definitions -h 127.0.0.1
```

先完成一次管道运行以创建存储和 manifest，再打开本机 `http://127.0.0.1:3000`。当前任务按需执行，尚未配置 Dagster 定时器；每周三次的开发自动任务与数据调度是两回事。
