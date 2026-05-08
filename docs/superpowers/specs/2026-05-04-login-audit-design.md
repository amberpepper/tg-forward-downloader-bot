# 登录审计设计

日期：2026-05-04

## 目标

为 Web 管理后台增加独立的登录审计能力，记录并展示：

- 登录成功
- 登录失败

审计记录需要在 WebUI 中单独展示，不与现有业务事件混用，并且包含来源信息：

- 用户名
- IP
- User-Agent

## 范围

本次仅覆盖 Web 后台 `POST /login` 的审计记录。

包含：

- 正确账号密码登录成功
- 错误账号或密码登录失败
- 登录日志分页查询
- WebUI 独立“登录日志”页展示

不包含：

- `/logout` 审计
- 密码或密码摘要记录
- 登录限流、封禁、验证码
- 单独的统计图表

## 方案选择

采用独立存储方案，新建 `login_logs` 表，不复用现有 `events` 表。

原因：

- 登录审计和下载/消息事件属于不同职责
- 后续按 IP、用户名、成功/失败做筛选更直接
- 后续如需扩展风控或审计字段，不会污染现有事件模型

## 数据模型

新增 SQLite 表 `login_logs`。

建议字段：

- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `created_at TEXT NOT NULL`
- `username TEXT`
- `success INTEGER NOT NULL`
- `ip TEXT`
- `user_agent TEXT`
- `failure_reason TEXT`

字段约束：

- `success` 使用 `1/0`
- `failure_reason` 在成功登录时为空
- 失败时固定写 `invalid_credentials`
- `username` 记录用户提交的用户名
- 不记录密码，不记录密码哈希，不记录任何密码派生信息

建议索引：

- `INDEX idx_login_logs_created_at ON login_logs(created_at DESC)`
- `INDEX idx_login_logs_success_created_at ON login_logs(success, created_at DESC)`

## 写入行为

在 `POST /login` 中增加审计写入。

### 登录成功

当用户名和密码与配置匹配时：

- 正常设置登录 cookie
- 写入一条成功记录
  - `username`: 提交的用户名
  - `success`: `1`
  - `ip`: 解析后的客户端 IP
  - `user_agent`: 请求头中的 User-Agent
  - `failure_reason`: `NULL`

### 登录失败

当用户名或密码不匹配时：

- 返回现有登录失败页面
- 写入一条失败记录
  - `username`: 提交的用户名
  - `success`: `0`
  - `ip`: 解析后的客户端 IP
  - `user_agent`: 请求头中的 User-Agent
  - `failure_reason`: `invalid_credentials`

## 请求来源解析

### IP

IP 获取顺序：

1. 优先读取 `X-Forwarded-For`
2. 如果存在多个值，取第一个非空地址
3. 如果没有代理头，则取请求直连地址
4. 都没有则记录为空

该行为用于兼容反向代理部署。

### User-Agent

- 原样记录 `User-Agent`
- 允许为空
- 不在后端做长度裁剪
- WebUI 负责省略显示和悬停查看全文

## 容错策略

登录审计属于旁路能力，不允许影响主登录流程。

规则：

- 写登录日志失败时，登录接口仍按原逻辑返回成功或失败
- 后端应记录应用日志，便于排查数据库写入异常

## 后端接口

新增：

- `GET /api/login-logs`

返回结构与现有列表接口风格保持一致：

```json
{
  "items": [],
  "page": 1,
  "page_size": 20,
  "total": 0,
  "pages": 1,
  "q": "",
  "success": ""
}
```

### 查询参数

- `page`
- `page_size`
- `q`
- `success`

查询规则：

- `q` 同时匹配 `username`、`ip`、`user_agent`
- `success` 支持空值、`true`、`false`
- 结果默认按 `created_at DESC`

权限要求：

- 必须已登录 Web 管理后台
- 与现有 `/api/jobs`、`/api/events` 使用相同会话校验方式

## WebUI 设计

新增独立标签页：`登录日志`

不复用“事件日志”页，不并入现有事件列表。

### 筛选区

- 搜索框：搜索用户名 / IP / User-Agent
- 状态筛选：全部 / 成功 / 失败
- 搜索按钮

### 表格列

- 时间
- 结果
- 用户名
- IP
- User-Agent
- 失败原因

展示规则：

- 结果列使用 badge 区分成功和失败
- `User-Agent` 默认单行省略显示
- 鼠标悬停时可查看完整 `User-Agent`
- 成功记录的失败原因列为空

### 分页

保持与“任务列表”“事件日志”相同的分页交互：

- 上一页
- 下一页
- 当前页 / 总页数
- 总记录数

## 测试范围

至少覆盖：

1. 错误账号或密码登录后，数据库新增一条失败记录，`success=0`，`failure_reason=invalid_credentials`
2. 正确账号密码登录后，数据库新增一条成功记录，`success=1`
3. 成功和失败记录都能写入 `username`、`ip`、`user_agent`
4. `GET /api/login-logs` 支持分页、关键字搜索、成功/失败筛选
5. WebUI“登录日志”页能正确展示列表，并支持筛选和翻页

## 实施说明

预期改动点：

- `app/store.py`
  - 初始化 `login_logs` 表
  - 增加写入与分页查询方法
- `app/main.py`
  - 在 `POST /login` 中写入登录审计
  - 新增 `GET /api/login-logs`
  - 增加 IP / User-Agent 提取辅助逻辑
- `app/templates/index.html`
  - 新增“登录日志”标签页
  - 新增列表渲染、筛选、分页逻辑

## 已确认决策

- 记录“登录成功 + 登录失败”
- 记录来源信息 `IP` 和 `User-Agent`
- 使用独立 `login_logs` 表
- 单独提供 WebUI 页面展示
- 不记录退出登录
- 失败原因统一为 `invalid_credentials`

## 风险与后续

当前设计未处理以下问题：

- 高频失败登录告警
- IP 黑名单或失败次数限制
- 多管理员账户体系

这些不在本次范围内，后续如需要，可基于 `login_logs` 扩展。
