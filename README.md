# 多源数据采集与RPA自动化平台

面向网页抓取、API接入、增量采集、RPA流程编排、数据校验与采集监控的数据工程平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 数据源配置 | `datasource` | 数据源 | 源端编号、连接类型、接入地址 |
| 网页采集规则 | `crawler` | 采集规则 | 规则编号、目标网址、页面选择器 |
| API接入管理 | `apiclient` | API客户端 | 客户端编号、接口名称、请求方法 |
| 增量采集 | `incremental` | 增量游标 | 游标编号、来源标识、增量字段 |
| RPA任务编排 | `robot_task` | RPA任务 | 任务编号、任务名称、页面定位器 |
| 数据接入通道 | `ingestion` | 接入通道 | 通道编号、通道名称、输入格式 |
| 格式解析器 | `parser` | 解析配置 | 解析编号、输入格式、输出格式 |
| 采集结果校验 | `validator` | 校验规则 | 规则编号、校验字段、校验类型 |
| 采集行定位 | `location` | 定位记录 / 缺项处置 | 定位编号、来源编号、运单号、物理行 |
| 采集去重 | `dedup` | 去重策略 | 策略编号、输入来源、去重键 |
| 采集调度 | `schedule` | 调度计划 | 计划编号、关联任务、调度周期 |
| 采集监控 | `monitor` | 监控面板 | 面板编号、采集源总数、今日采集量 |
| 采集日志 | `log_viewer` | 日志条目 | 日志编号、关联任务、日志级别 |
| 凭证管理 | `auth` | 认证凭证 | 凭证编号、凭证类型、关联数据源 |
| 采集转换管道 | `transform` | 转换节点 | 节点编号、输入来源、输出目标 |
| 数据流转编排 | `flow` | 流转管道 | 管道编号、管道名称、起始节点 |
| 数据血缘追踪 | `lineage` | 血缘关系 | 关系编号、上游节点、下游节点 |
| 采集报告 | `report` | 采集报告 | 报告编号、采集源、统计周期 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 采集行定位与缺项处置

数据接入与采集结果校验共用一套「采集行定位」记录，不再各存一份原文位置。

- **来源解析**：`backend/app/services/dsv.py` 按引号状态机切分正文，只有引号外的
  换行才是记录边界；引号内的换行属于字段值，所以带引号的多行地址不会被拆成
  两条记录。引号未闭合等异常也会保留已经读到的字段与行、列，`解析状态` 记为
  `解析异常`，供校验人员按原文核对。
- **定位数据集**：`row_location` 表每条记录对应一个数据行，`字段定位` 里写明每个
  字段的起始行、起始列、结束行、结束列与字符偏移。接入接口
  `POST /api/ingestion/{id}/parse` 解析后返回的 `定位编号`，就是采集结果校验
  `POST /api/validator/{id}/check` 生成缺项处置时引用的同一条记录。
- **缺项处置**：`disposition` 表按 `运单号 + 字段名` 归并同一票货。重投样本里同一
  缺项仍存在时，不新建处置，而是把最新位置挂到已有处置的 `复核批次`，旧位置进
  `历史位置`。
- **复核回放**：`GET /api/location/dispositions/{id}/locate` 按原处置号返回产生时的
  物理行，同时可按 `sample_no` 给出新样本上的当前位置；`GET /api/location/replay`
  只靠当时的样本编号、记录序号与字段名就能回到原位置。
- **再次校验**：`POST /api/location/recheck` 默认只做诊断，返回 `can_cover`（手上
  核查能否覆盖最新位置）与 `should_claim`（还应领取的待办）；`auto_claim=true`
  时同步领取与位置。

示例数据里 `UPLD-0001` 是首版货单（含标题行），`UPLD-0002` 是重投版（删去标题行，
并把第二票一个带引号的多行地址拆到两行），两版运单号一致、第一票邮编都没有更正。
删掉标题行后第一票从第 2 行前移到第 1 行，第二票的地址跨第 2-3 行、邮编落在第 3 行。
复核员领取待办后再校验，就能看到原位置与新位置的对照以及覆盖结论。
`UPLD-0003` 故意留了未闭合引号，用来演示抽取失败仍保留原文位置。

回归用例：`cd backend && python3 -m pytest -q`。
