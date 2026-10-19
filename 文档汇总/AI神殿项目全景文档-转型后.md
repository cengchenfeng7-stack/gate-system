# AI 神殿 · 项目全景文档（转型后 · v2.8）

> 项目位置：`E:\智能体ai`
> 当前版本：v2.8
> 最后更新：2026-10-19
> 文档定位：本文档只描述“转型之后”的完整产品状态，不再复述早期排错过程。
> 相关文档：逐版变更见 `更新日志.md`；历史演进与排错过程见 `AI神殿项目全景文档.md`。

---

## 目录

1. [转型定位](#一转型定位)
2. [总体架构](#二总体架构)
3. [能力全景](#三能力全景)
4. [数据模型](#四数据模型)
5. [API 地图](#五api-地图)
6. [配置项](#六配置项)
7. [安全与护栏](#七安全与护栏)
8. [验收与实测](#八验收与实测)
9. [版本演进](#九版本演进)
10. [已知限制与 v2.9 路线](#十已知限制与-v29-路线)
11. [文件地图](#十一文件地图)
12. [常见问题与排错要点](#十二常见问题与排错要点)
13. [附录](#十三附录)

---

## 一、转型定位

### 1.1 从什么转到什么

| 维度 | 转型前 | 转型后 |
|------|--------|--------|
| 产品形态 | 本地多 Agent 群聊工具 | 本地 AI 工作台 / 多智能体协作平台 |
| 主界面 | 单页聊天 + 固定成员卡片 | 助手库 + 多标签 + 工具栏 + 设置/用量/工具面板 |
| 交互模式 | 群聊为主 | 9 种模式：群聊 / 私聊 / 工作流 / 主动服务 / 持续对话 / 创作 / 数据分析 / 自主代理 / 跨平台 |
| 模型接入 | 单一配置 | 44 个提供商预设 + 任意自定义 Base URL + 模型能力标签 |
| 自主性 | 被动响应 | L2 主动巡检、画像、建议反馈；L3 任务队列、失败重规划、策略沉淀、长程目标 |
| 工具体系 | 少量内置工具 | 文件 / 命令 / 知识库 / GUI / MCP / 插件声明式工具，统一权限与开关 |
| 可观测性 | 日志为主 | 审计日志、Token 用量、成本估算、缓存命中率、每日热力图 |
| 产品化 | 无统一设置 | 设置中心、用量中心、工具页、应用内更新日志 |

### 1.2 转型后的产品形态

可以用一句话概括：

> 一个运行在本机的 AI 工作台：既能像聊天客户端一样管理助手、模型和会话，也能让 Agent 真正读写文件、执行命令、操作桌面、查知识库、排队执行长任务，并把执行过程记录下来。

### 1.3 能力边界

已经做到的：

- 多提供商、多模型、多模态图片理解
- 多 Agent 群聊与 1v1 私聊
- 真实工作流、真实自主任务、真实工具调用
- 本地知识库、长期/短期记忆、知识图谱
- 主动巡检、用户画像、建议反馈学习
- 任务队列、失败重规划、策略复用、长程目标验收
- 设置中心、用量中心、工具页、更新日志
- 后台管理、数据库管理、MCP、插件、沙箱
- 家庭成员版访问口令（单口令，不是账号体系）
- 编码搭档：本机 CLI 编码 Agent 接入
- 笔记与 AI 工作日志
- 按用途分配默认模型
- 页面内划词助手：翻译 / 解释 / 总结 / 存笔记 / 复制
- 群聊管理：群资料、8 位群号、独立 AI 成员、成员权限、置顶/免打扰/折叠、人数扩容
- 群聊体验：@ 指定成员、消息搜索、群文件、未读计数与已读
- 群聊深化：引用回复、群公告置顶、成员发言频率控制
- 插件工具可视化编排：表单化配置 HTTP / 命令工具，免手写 JSON
- 开放 API + Webhook：外部对话、后台任务、事件推送
- 桌面壳：托盘常驻、开机自启、后台服务状态与系统通知
- 桌面壳增强：全局快捷键唤起工作台、全局划词、托盘未读角标

暂未做到的：

- 多用户 / 多租户 / 账号体系（明确不做，改为单口令家庭成员版）
- 语音输入输出、移动端 App / 小程序（明确不做）
- 自动获取各提供商实时价格（成本单价需手动填写）

---

## 二、总体架构

### 2.1 运行架构

```
┌──────────────────────────────────────────────────────────────┐
│                        浏览器前端                            │
│  index.html（主工作台）      admin.html（后台管理）           │
│  助手库 / 多标签 / @引用     知识库 / 模型 / 审计 / 数据库     │
│  设置中心 / 用量中心 / 工具页 / 更新日志 / 开放接口            │
└───────────────────────────┬──────────────────────────────────┘
                            │ HTTP + SSE
┌───────────────────────────▼──────────────────────────────────┐
│                    app/server.mjs（Node + Express）           │
│  路由 / SSE / 会话 / 直连模型 / 工具循环 / 审计 / 沙箱 / MCP    │
│  设置中心 API / 用量统计 / 工具页 API / 开放 API / Webhook     │
└───────┬──────────────┬──────────────┬──────────────┬──────────┘
        │              │              │              │
┌───────▼──────┐ ┌─────▼──────┐ ┌─────▼──────┐ ┌─────▼─────────┐
│ providers.mjs│ │ tools.mjs  │ │ autonomy-  │ │ proactive-    │
│ 流式模型调用  │ │ 工具+权限  │ │ service.mjs│ │ service.mjs   │
│ 工具循环      │ │ 沙箱+GUI   │ │ 巡检/队列  │ │ 记忆/学习     │
│ Token 采集    │ │ 路径护栏   │ │ 策略/目标  │ │ 主动服务      │
└───────┬──────┘ └─────┬──────┘ └─────┬──────┘ └─────┬─────────┘
        │              │              │              │
        └──────────────┴──────┬───────┴──────────────┘
                              │
                    ┌─────────▼─────────┐
                    │ SQLite data/chat.db│
                    │ WAL + 27 张业务表  │
                    └───────────────────┘
```

### 2.2 代码分层

| 层 | 文件 | 职责 |
|----|------|------|
| 主服务 | `app/server.mjs` | 路由、SSE、会话、直连模型、工具循环、审计、沙箱、MCP、设置/用量/工具 API |
| 模型引擎 | `app/engine/providers.mjs` | OpenAI 兼容 / Claude / Ollama 流式调用、工具循环、Token 与缓存采集 |
| 工具执行 | `app/engine/tools.mjs` | 工具定义、权限、路径护栏、命令黑名单、沙箱、GUI 工具、工具开关 |
| 自主性 | `app/engine/autonomy-service.mjs` | 后台巡检、用户画像、建议反馈、任务队列、策略库、长程目标 |
| 记忆/学习 | `app/engine/proactive-service.mjs` | 主动服务、持续对话、层次记忆、自主学习 |
| 提示词/审计 | `app/engine/prompt-config.mjs` | 系统/步骤/风格提示词、智力六档、审计日志 |
| 提供商配置 | `app/engine/providers-service.mjs` | 44 个预设读取、API Key/Base URL/模型保存、模型目录拉取 |
| 模型能力 | `app/engine/model-capabilities.mjs` | 文本/看图/视频/思考/工具/嵌入六维标签 |
| 商店 | `app/engine/store-service.mjs` | 技能/插件/MCP 条目的 CRUD、启停、导入导出 |
| 插件工具 | `app/engine/plugin-tools.mjs` | 插件 `config.tools` 的 HTTP / command 声明式工具 |
| MCP | `app/engine/mcp-client.mjs` | stdio / SSE / Streamable HTTP 连接池与工具调用 |
| 桌面自动化 | `app/engine/uia.mjs` / `screen.mjs` | 窗口枚举、控件树、点击、输入、截图 |
| 数据库迁移 | `app/engine/db-migration.mjs` | 幂等建表/补列 |
| 主界面 | `app/dist/index.html` | 助手库、多标签、@引用、设置、用量、工具、更新日志 |
| 后台 | `app/dist/admin.html` | 12 个管理模块 |

### 2.3 数据流

**普通对话**

```
用户输入
  → /api/chat
  → 选择模式（群聊/私聊/工作流/…）
  → runDirectTurn 或群聊引擎
  → providers.mjs 流式调用模型
  → 模型请求工具 → tools.mjs 执行 → 结果回填 → 继续生成
  → SSE 推送 text / tool_start / tool_end / confirm_request / done
  → 写入 messages / audit_logs / token_usage / context_memory
```

**后台自主任务**

```
/api/autonomous/queue 入队
  → TaskQueueService 每 30 秒领取 due 任务
  → runAutonomousAgent（空 SSE sink）
  → 规划 → 逐步执行 → 工具验证 → 学习评分 → 失败重规划 → 策略沉淀
  → 完成/暂停写回 autonomous_tasks
  → 回写会话消息 + 目标进度 + 验收
```

**主动巡检**

```
ProactivePatrol 定时触发
  → 扫描近 7 天活跃会话
  → 重复主题 / 知识库缺口 / 超时任务 / 暂停任务 / 目标到期
  → 建议去重 + 反馈权重
  → 写入 proactive_services
  → 前端建议卡片（执行 / 忽略）
```

### 2.4 启动与部署

- 启动：双击 `启动.bat`，或运行 `runtime\node.exe app\server.mjs`
- 停止：`停止.bat`
- 默认地址：`http://127.0.0.1:3000`
- 后台：`http://127.0.0.1:3000/admin.html`
- 数据：`data/chat.db`（SQLite WAL）
- 日志：`logs/server.log`、`logs/server.err.log`
- 工作区：`workspace/`，截图在 `workspace/screenshots/`
- 无需外部数据库；模型通过当前激活的提供商访问

---

## 三、能力全景

### 3.1 主界面

**左侧助手库**

- 搜索助手（按名称/角色）
- 助手卡片：名称、角色、头像色、在线点
- 点击助手进入 1v1 私聊标签
- 新建对话、历史会话、设置中心、用量中心、工具页入口

**多标签**

- 新建 / 切换 / 关闭标签
- 每个标签独立保存：会话、模式、模型、工作目录、草稿、引用、待发图片
- 每个标签独立消息 DOM，流式输出按标签归属，切换不串台
- 标签状态写入 `localStorage`，刷新恢复
- 会话生成中禁止再次发送/关闭该标签，避免并发流冲突
- 已删除会话的旧标签会自动重置为“新对话”

**顶部工具栏**

- 会话模式（9 种）
- AI 模型（动态加载）
- 智力强度（6 档）
- 工作目录（workspace / 项目根 / 自定义）
- 权限面板
- 刷新模型
- 后台入口

**输入区**

- 文本输入、Enter 发送
- 图片上传与多模态理解
- `@` 引用：工作区文本文件 + 知识库条目
- 引用 chip，删除 chip 同步删除文本
- 档位 2 写操作确认卡

### 3.2 九种会话模式

| 模式 | 行为 |
|------|------|
| 群聊 | 意图识别 → 选择发言 Agent → 多 Agent 发言 → 主持总结 |
| 私聊 | 指定 Agent 单轮/多轮对话，真实工具调用 |
| 工作流 | 分析 → 执行 → 总结；支持代码审查、文件操作、自定义模板 |
| 主动服务 | 规则分析 + 建议卡片 + 真实群聊执行 + 执行摘要 |
| 持续对话 | 读取活跃任务，真实执行，检查任务完成 |
| 创作协作 | 创意构思 → 大纲 → 内容 → 润色，四轮真实创作 |
| 数据分析 | 数据收集 → 分析 → 报告，真实读文件/工具 |
| 自主代理 | 自规划 → 逐步执行 → 工具验证 → 学习 → 进化 |
| 跨平台 | 按目标平台生成适配内容 |

### 3.3 多提供商与模型

- 44 个提供商预设：OpenAI、Anthropic、DeepSeek、Kimi、OpenRouter、SiliconFlow、智谱、百川、豆包、MiniMax、Groq、NVIDIA、腾讯、商汤等
- 支持 OpenAI 兼容协议、Claude 协议、Ollama 本地模型
- 自定义 Base URL、API Key、默认模型
- 模型目录动态拉取，失败回退预设
- 六维能力标签：文本 / 看图 / 视频 / 思考 / 工具 / 嵌入
- 视觉模型带 tools 会吞文本时自动禁用 tools
- 当前激活提供商：以 `AI_PROVIDER` 为准

### 3.4 工具与权限

**内置工具**

| 分类 | 工具 |
|------|------|
| 文件 | read_file / write_file / edit_file / list_directory / search_files |
| 命令 | execute_command |
| 知识库 | search_knowledge_base / add_knowledge |
| 桌面 | gui_windows / gui_tree / gui_find / gui_click / gui_type / gui_screenshot |
| 扩展 | MCP 工具、插件声明式工具 |

**三档权限**

| 档位 | 行为 |
|------|------|
| 1 只读 | 只允许读取类工具 |
| 2 变更确认 | 写操作返回确认卡，用户允许后执行 |
| 3 极速自动 | 全部工具直接执行 |

**工具开关**

- 设置中心 → 工具：文件工具 / 终端命令 / 桌面 GUI / 屏幕截图 / 文件沙箱
- 关闭后不再下发给模型；模型强行调用也会被 `executeTool` 拦截
- `true/false/1/0` 均可识别，保存后下一条消息立即生效

**沙箱**

- 文件级试错沙箱，不是虚拟机
- 开启后写入落 `data/sandbox`，支持提交/清空
- 未开启时所有路径必须落在工作区，禁止 `../` 越界
- 危险命令黑名单 + cwd 限制

### 3.5 知识库与记忆

**知识库**

- FAQ 条目：问题、答案、分类、关键词、命中数、最后命中时间
- FTS5 全文索引 + 朴素匹配兜底
- 搜索打分：覆盖率 + 权重
- 批量操作、导入导出、索引重建
- Agent 工具 `search_knowledge_base` / `add_knowledge`
- 主界面 `@` 引用知识库条目

**记忆**

- 短期记忆：`context_memory`，1 小时过期
- 长期记忆：任务经验总结，跨会话注入
- 记忆自动注入群聊 / 私聊 / 直连模式的系统提示词
- 跨会话用户画像：主题词 + 高频意图，6 小时刷新

**知识图谱**

- 从知识库重建实体与关系
- 力导向可视化
- 实体/关系增删改

### 3.6 自主性 L2 / L3

**L2 主动建议**

- 后台定时巡检，不依赖用户发消息
- 检查：重复主题、知识库缺口、超时未推进任务、暂停的自主任务、目标到期
- 建议去重、优先级、反馈权重
- 建议卡片：执行 / 忽略
- 反馈写入 `feedback`，影响后续排序
- 巡检日志：扫描会话数、新增建议数、自动重排数

**L3 自主执行**

- 任务队列：`queued → running → completed/paused`
- 后台调度每 30 秒检查一次
- 服务重启自动恢复中断任务
- 暂停任务 10 分钟后自动重试（最多 2 次）
- 失败重规划：模型改写剩余计划，最多 2 次
- 策略沉淀：≥2 步且平均评分 ≥0.8 写入 `strategy_library`
- 策略复用：新任务规划时按关键词匹配并注入
- 长程目标：验收标准、截止时间、关联任务、进度回算、模型验收

**评分**

- 0：失败或空谈
- 0.2：仅汇报
- 0.4：1 次只读
- 0.6：≥2 次只读
- 0.8：1 次写入
- 1：写入 + 复验，或 ≥2 次写入

### 3.7 设置中心

主界面左侧「设置中心」，5+1 个页签：

- 模型服务：44 提供商、搜索、API Key 脱敏回填、Base URL、默认模型、同步模型、保存并启用
- 工具：五类工具开关 + 扩展计数
- 偏好：默认智力档位、群聊最多发言成员、新对话默认模式、默认工作目录、家庭访问、模型分工
- 用量：见 3.8
- 更新日志：读取 `更新日志.md`，按版本折叠
- 关于：版本、Node、数据存储、工作区、后台入口

### 3.8 用量中心

- 8 张指标卡：总成本、请求数、总 Token、缓存命中率、活跃天数、峰值日、最常用模型、日均 Token
- 4 条迷你折线：成本 / 请求 / Token / 缓存命中率
- 每日活动热力图：30 / 90 / 365 天，按 Token 分 5 档
- 模型用量表：请求 / Token / 缓存 / 成本
- 计费单价：输入 / 输出 / 缓存命中，CNY / USD
- 成本公式：

```
成本 =（非缓存输入 Token × 输入单价
      + 缓存输入 Token × 缓存单价
      + 输出 Token × 输出单价）/ 1,000,000
```

- 未配置单价时成本显示 0，避免编造价格
- 缓存 Token 兼容 `prompt_tokens_details.cached_tokens`、`prompt_cache_hit_tokens`、`cache_read_input_tokens`

### 3.9 工具页

主界面左侧「工具」：

**翻译**

- 原文/译文双栏、8 种目标语言、字符计数
- 复制译文、用译文替换原文
- 独立接口，不创建会话、不写聊天历史
- 记录 `mode=translate` 的 Token 用量
- 轻量调用禁用工具

**知识库**

- 统计卡、列表、搜索命中度、新增/编辑/删除
- 导入/导出、重建 FTS 索引

**定时任务**

- 会话、目标、执行时间、优先级、是否允许后台写操作
- 循环规则：不循环 / 每天 / 每周 / 每月；指定 `HH:MM`，每周选星期，每月选日期
- 不填首次执行时间时，服务端自动算出下一个未来时刻
- 失败自动重试：默认 2 次，60 秒 / 120 秒退避，可设 0~5 次
- 执行超时：默认 15 分钟，可设 1~120 分钟，步骤边界检查
- 服务重启后中断任务自动回队，按检查点续跑
- 错过补跑：过期任务立即执行一次，然后跳到下一个未来周期
- 列表显示循环、下次执行、状态、重试次数、进度，支持重试/放弃

**截图**

- 一键截取当前屏幕，保存到 `workspace/screenshots`
- 页面预览、打开原图、最近截图列表
- 截图文件带路径越界校验
- 需在设置中心开启桌面 GUI / 屏幕截图

**划词助手（页面内）**

- 在页面任意文本上选中 2~2000 字符，浮出工具条
- 五个动作：翻译 / 解释 / 总结 / 存笔记 / 复制
- 翻译自动判断方向：含中文译成 English，否则译成简体中文
- 解释 / 总结走独立接口与「快速模型」，不创建会话、不写入聊天历史
- 结果卡片支持复制结果、Esc 或 ✕ 关闭
- 存笔记会打开笔记页并预填，人工确认后保存
- 输入框、文本域、下拉框、可编辑区域内不触发

**编码搭档**

- 接入本机 CLI 编码 Agent：Claude Code / OpenAI Codex / OpenCode / Qwen Code / Kimi Code / 自定义 CLI
- 自动检测安装状态与版本（`where` / `which`，缓存 60 秒）
- 工作目录：项目根或工作区；命令模板支持 `{goal}`、`{model}`
- SSE 实时输出 stdout / stderr，可一键停止
- 自动注入当前提供商 API Key / Base URL / 模型
- 结束后自动生成「编码任务」笔记（`agent` 可见性）并写 AI 工作日志
- 最近运行列表保留 30 条，含状态、退出码、关联笔记

**笔记**

- 「我的笔记 / AI 工作日志」两个子页
- 三种可见性：`private` 仅人可读；`shared` 人机共读；`agent` AI 工作记录
- 新建、编辑、删除、置顶、标签、搜索、JSON 导入 / 导出
- AI 工具 `note_write` / `note_read` / `note_search`，读不到 `private`

**AI 工作日志**

- 记录自主任务开始、规划、步骤完成 / 失败、重规划、暂停、完成、结束
- 记录编码任务结束事件与产出笔记
- 供长期自主运行追溯，人类可读、AI 可写

### 3.10 家庭成员版

定位：只给家人和信得过的人用，不做账号、多租户、小程序。

**访问口令**

- `FAMILY_ACCESS_PASSWORD` 单口令；留空则关闭
- 登录后写入 HMAC-SHA256 签名 Cookie，`HttpOnly` + `SameSite=Lax`，30 天
- 未登录访问数据接口返回 401 `FAMILY_AUTH_REQUIRED`
- 登录失败限流：10 分钟 8 次
- 默认 `BIND_HOST=127.0.0.1` 仅本机；家人访问需改为 `0.0.0.0` 并重启
- 设置中心可启用 / 修改口令 / 退出登录

**模型分工**

- 五类用途：默认对话、快速意图、翻译、视觉、编码
- 存于 `app_settings.model_routes`，留空跟随默认
- 翻译、图片理解、编码搭档、快速意图已按分工取模型

### 3.11 后台管理

`admin.html` 的 12 个导航模块：

- 数据概览
- 智能体管理
- 知识库管理
- 模型配置
- 对话记录
- 系统设置
- 提示词与智力
- 执行日志
- Agent 商店
- 知识图谱
- 数据库
- 自主性 L2/L3

工作流模板编辑在「系统设置」内。

后台能力：

- 数据库表浏览、只读 SQL、维护、备份
- 审计日志与 Token 统计
- 沙箱状态、提交、清空
- MCP 连接测试与工具调用
- 插件工具测试
- 知识库导入导出、FTS 重建
- 知识图谱编辑
- 自主性巡检、画像、队列、策略、目标

### 3.12 群聊管理

**群资料**

- 群头像（emoji）、群名称、群分类、群标签、群介绍、群地点/主题
- 群公告、我的本群昵称、群备注（仅自己可见）
- 8 位唯一群号，旧群迁移时自动回填
- 群列表搜索支持群号、群名、标签、分类、介绍、备注

**群成员**

- 每个群独立的 AI 成员列表，不再所有群共用同一批
- 添加、移出、停用、待确认
- 成员权限五项：发言 / 工具 / 写文件 / 命令 / 笔记
- 关闭「发言」不参与群聊；关闭「工具」不下发工具；其余在 `executeTool` 层拒绝
- 新 AI 成员加入策略：手动 / 自动 / 需要确认

**容量与列表**

- 默认上限 20，可一键扩容到 50 / 100 / 200（本地无付费）
- 置顶、免打扰、折叠、保存到通讯录
- 折叠群聊收进列表底部，可展开/收起；搜索时自动包含
- 列表项显示头像、群号、成员数、消息数、分类与标签徽标

**入口**

- 左侧侧栏「群聊 / 助手」双视图，默认群聊
- 常驻群列表：头像、群名、最后消息预览、时间、未读徽标、置顶/免打扰标识，当前群高亮
- 点击直接进群；每项「⋯」打开群管理
- 「＋ 新建群聊」：群名/头像/分类/介绍 + 勾选 AI 成员，创建后自动打开
- 聊天头部「👥 群管理」
- 「🔍 全部群聊」保留为高级搜索入口（群号/群名/标签/介绍/备注）
- 群管理面板：群资料 / 群成员 / 群管理 / 群文件四个页签

**@ 指定成员**

- 输入 `@` 弹出引用菜单，「群成员」分段列出本群 AI 成员
- 支持全名与简称（`全栈开发 · 阿码` → `@阿码`）
- 被 @ 的成员强制进入发言队列，即使意图识别未选中
- SSE 下发 `mentions` 事件，前端提示已 @ 的成员

**消息搜索**

- 聊天头部 🔎 打开搜索面板，关键词 + 当前群 / 全部群
- 结果高亮关键词，点击跳转到对应消息并定位高亮

**群文件**

- 群管理「群文件」页签：名称、大小、上传时间
- 上传（base64，单个 ≤ 10MB）、下载、删除
- 落盘 `workspace/group-files/<sessionId>`，下载前做路径越界校验

**未读与已读**

- `sessions.last_read_at` + 非用户消息计数得到每群未读数
- 打开群即标记已读；侧栏未读徽标与站内通知取最大值合并展示

**引用回复**

- 消息悬停出现「↩ 回复」，输入框上方显示回复条，可取消
- 发送后气泡内显示引用块（发送者 + 原文摘要），点击定位原消息并高亮
- 被引用内容以最多 300 字进入模型上下文；`messages.reply_to_id` 落库
- 仅在群聊模式启用，显式 @ 与意图识别不受引用影响

**群公告置顶**

- 群公告非空时，聊天区顶部常驻「📌 群公告」横幅
- 支持收起 / 展开，状态按群保存在 localStorage
- 修改群资料中的公告后即时刷新；仅群聊模式显示

**成员发言频率**

- `sessions.speech_mode` 四档：克制（1 位）/ 标准（2 位）/ 活跃（3 位）/ 仅 @ 时发言
- 仅 @ 时：无人 @ 只回应 1 位，被 @ 的成员始终优先
- 显式 @ 的数量可以突破当前档位上限
- 入口：群管理 → 群管理 → 成员发言频率

### 3.13 插件可视化编排与开放 API

**插件可视化编排**

- 设置中心「插件」页：表单化编辑器，不再手写 JSON
- 工具字段：名称、说明、只读、参数行（名称 / 类型 / 必填 / 说明）
- 执行方式：
  - HTTP：URL、方法、Headers、Body 模板（`{{参数}}` 占位）
  - 命令：命令 + 参数行（`{{参数}}` 占位）
- 支持保存、测试调用、启用/停用、删除；多工具插件保存时保留其余工具
- 启用后工具真实进入模型工具列表，调用时按成员权限与只读标记执行

**开放 API**

- 设置中心「开放」页：总开关、API Key（生成 / 复制 / 重置）、Webhook 地址与开关、调用示例
- 鉴权头 `X-API-Key`，使用 `timingSafeEqual` 常量时间比较
- 能力：健康检查、外部对话、创建/查询后台任务
- 关闭总开关后所有开放接口返回 403

**Webhook**

- 事件：`webhook.test`、`task.finished`、`coding.finished`
- POST JSON，8 秒超时，带 `X-AI-Temple-Event` 头
- 仅推送事件，不重试；失败不影响主流程

### 3.14 数据备份与通知

**备份与恢复**

- 设置中心「数据」页：自动备份开关、手动备份、备份列表、恢复、删除
- 备份内容：SQLite（群聊、笔记、日志、任务、通知、设置）+ 可选工作区
- 自动备份：启动后检查，每天最多一份，保留最近 10 份
- 恢复前自动生成安全备份；数据库逐表回写，工作区合并覆盖；重建 FTS
- 备份目录：`data/backups/`

**站内通知**

- 侧栏 🔔 未读徽标，通知面板，全部已读，桌面通知
- 后台任务完成/暂停、编码任务完成/失败自动通知
- 群列表显示未读数，打开群自动清空
- 每 30 秒刷新一次

### 3.15 桌面壳

**托盘宿主**

- `智能Agent群.exe` 由 `Launcher.cs` 编译，启动后静默拉起 `runtime/node.exe app/server.mjs`
- 托盘菜单：打开工作台 / 模型设置 / 管理后台 / 重启服务 / 查看日志 / 编辑配置 / 开机自启 / 全局快捷键设置 / 运行状态 / 退出
- 双击托盘图标打开或唤起工作台；退出时通过 Job Object 自动清理 Node 子进程
- 打开工作台时优先查找标题含「AI 神殿」的浏览器窗口并置前，找不到才新开页面

**全局快捷键（v2.8）**

- `Ctrl+Alt+Space`：唤起工作台；工作台已在前台时最小化
- `Ctrl+Alt+X`：对当前前台应用捕获选中文本，进入全局划词
- 快捷键可在托盘菜单「⌨️ 全局快捷键设置…」中重新按键录制，保存在 `data/desktop-shell.json`
- 组合键至少包含 Ctrl / Alt / Shift 之一；两个功能不允许使用相同组合；注册被占用时提示并恢复原设置

**全局划词（v2.8）**

- 触发方式：在任意应用中选中文本后按 `Ctrl+Alt+X`
- 捕获方式：备份剪贴板 → 向前台窗口发送 `Ctrl+C` → 读取剪贴板 → 恢复原剪贴板
- 文本通过本机接口 `POST /api/desktop/selection` 暂存，工作台页面每 2 秒轮询取走
- 取到后复用页面划词工具条：翻译 / 解释 / 总结 / 存笔记 / 复制
- 未检测到选中文本、应用不支持复制或后台服务未就绪时，托盘气泡提示

**托盘未读角标（v2.8）**

- 每 20 秒轮询通知接口时同步读取 `unreadCount`
- 有未读时在托盘图标右下角绘制红色角标与数字（超过 99 显示 `99+`）
- 托盘提示文字同步显示「N 条未读」，全部已读后角标自动消失

**开机自启**

- 菜单项带勾选状态，写入 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
- 注册表值为 `"<程序路径>" --silent`，登录后静默驻留托盘，不自动打开浏览器
- 也可用 `--enable-autostart` / `--disable-autostart` 脚本化配置

**系统通知**

- 托盘每 20 秒检查 `/api/health` 并拉取新通知
- 后台任务完成、编码任务完成/失败、服务断开/恢复弹出 Windows 通知
- 单次最多弹 3 条，避免刷屏
- 通知来源：本机专用接口 `GET /api/desktop/notifications?after=<ISO 时间>`，仅 `127.0.0.1` / `::1` 可访问

---

## 四、数据模型

SQLite `data/chat.db`，WAL 模式。共 27 张业务表 + 1 张 FTS5 虚拟表。

### 4.1 核心表

| 表 | 用途 | 关键字段 |
|----|------|----------|
| sessions | 会话 / 群 | id, title, model, mode, target_agent_id, proactive_enabled, context_memory_enabled, avatar, category, tags, intro, location, announcement, my_nickname, remark, pinned, muted, folded, saved, group_no, member_limit, join_policy, last_read_at, speech_mode, updated_at |
| session_agents | 群成员 | session_id, agent_id, enabled, permissions(JSON), joined_at |
| messages | 消息 | id, session_id, role, content, model, tool_calls, agent_id/name/color, intent, reply_source, reply_to_id |
| group_files | 群文件 | id, session_id, name, stored_path, size, mime, uploaded_by, created_at |
| agents | 智能体 | id, name, role, avatar, color, system_prompt, keywords, intents, enabled, sort |
| faq | 知识库 | id, question, answer, category, keywords, hits, enabled, last_hit_at |
| agent_sessions | Agent 会话映射 | session_id, agent_id, sdk_session_id |
| intent_logs | 意图日志 | id, session_id, query, intent, source, confidence, agents, faq_id |

### 4.2 自主性表

| 表 | 用途 | 关键字段 |
|----|------|----------|
| proactive_services | 主动建议 | id, session_id, service_type, trigger_condition, action, status, priority, weight, feedback |
| task_tracking | 持续对话任务 | id, session_id, task_name, status, progress, priority, due_date |
| context_memory | 长短期记忆 | id, session_id, memory_type, content, importance, expires_at |
| learning_records | 学习记录 | id, agent_id, interaction_type, input, output, feedback, score |
| autonomous_tasks | 自主任务/队列 | id, session_id, goal, plan, step_results, status, priority, due_at, goal_id, allow_writes, replan_count, auto_retry, max_retry, timeout_sec, recurrence, recur_time, recur_weekday, recur_monthday, origin |
| user_profiles | 跨会话画像 | id, summary, topics, intents, session_count, message_count |
| strategy_library | 策略库 | id, name, goal_pattern, keywords, steps, avg_score, use_count, source_task |
| agent_goals | 长程目标 | id, session_id, title, description, acceptance, deadline, status, progress, last_review_result |
| patrol_runs | 巡检日志 | id, started_at, finished_at, sessions_scanned, suggestions_created, auto_requeued |

### 4.3 知识、配置与运维表

| 表 | 用途 |
|----|------|
| knowledge_graph | 知识图谱实体 |
| knowledge_edges | 知识图谱关系 |
| workflow_templates | 工作流模板 |
| agent_configs | Agent 自主性配置 |
| prompt_config | 系统/步骤/风格提示词 |
| audit_logs | 工具执行审计 |
| token_usage | Token/缓存/上下文用量 |
| store_items | 技能/插件/MCP 商店 |
| notes | 笔记：`private` / `shared` / `agent` 三种可见性 |
| agent_journal | AI 工作日志：任务、编码、笔记事件 |
| coding_runs | 编码搭档运行记录：命令、输出、退出码、关联笔记 |
| notifications | 站内通知：类型、标题、正文、关联群、已读、时间 |
| app_settings | 应用设置（巡检开关/间隔、模型分工等） |
| faq_fts | FAQ FTS5 虚拟表 |

---

## 五、API 地图

### 5.1 健康、提供商与模型

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查、当前提供商、默认模型 |
| GET | `/api/provider` | 当前提供商 |
| GET | `/api/models` | 模型目录 + 能力标签 |
| GET | `/api/models/fetch` | 按 Base URL/Key 拉取远端模型 |
| GET | `/api/providers/config` | 全部提供商预设 + 当前提供商 |
| POST | `/api/providers/config` | 保存 API Key/Base URL/模型并启用 |
| GET | `/api/providers/active` | 当前提供商生效配置（Key 脱敏） |
| GET | `/api/providers/detail?key=` | 指定提供商配置详情 |
| GET | `/api/ollama/health` | 本地 Ollama 健康 |

### 5.2 会话、对话与意图

| 方法 | 路径 | 说明 |
|------|------|------|
| GET/POST | `/api/sessions` | 群列表（含成员数、最后消息、置顶排序） / 新建群（可指定 memberIds） |
| GET/DELETE | `/api/sessions/:id` | 群详情+消息 / 删除群 |
| PATCH | `/api/sessions/:id` | 群资料与管理字段（含 `speech_mode`） |
| GET/POST | `/api/sessions/:id/members` | 群成员列表 / 添加成员 |
| PATCH/DELETE | `/api/sessions/:id/members/:agentId` | 成员启停、权限 / 移出 |
| POST | `/api/sessions/:id/members/expand` | 群人数扩容 |
| POST | `/api/sessions/:id/read` | 标记该群已读 |
| GET | `/api/messages/search` | 消息搜索（`q` / `sessionId` / `limit`） |
| GET/POST | `/api/sessions/:id/files` | 群文件列表 / 上传（base64） |
| GET/DELETE | `/api/sessions/:id/files/:fileId` | 下载 / 删除群文件 |
| POST | `/api/chat` | 统一对话入口（SSE，9 模式；群聊支持 `replyToId`） |
| GET | `/api/intent/defs` | 意图定义 |
| POST | `/api/intent/recognize` | 意图识别 |
| GET/POST | `/api/agents`；`PATCH/DELETE /api/agents/:id` | 智能体管理 |
| GET | `/api/faq`、`/api/faq/search`、`/api/faq/stats` | 知识库列表/搜索/统计 |
| POST/PATCH/DELETE | `/api/faq`、`/api/faq/:id` | 知识库增改删 |
| GET | `/api/faq/export` | 导出 |
| POST | `/api/faq/import` | 导入 |
| POST | `/api/faq/rebuild-fts` | 重建索引 |

### 5.3 工作流、提示词、审计、Token

| 方法 | 路径 | 说明 |
|------|------|------|
| GET/POST | `/api/workflow-templates`；`DELETE /api/workflow-templates/:id` | 工作流模板 |
| GET/POST | `/api/prompts` | 提示词配置 |
| GET/POST | `/api/intelligence` | 智力六档 |
| GET | `/api/audit-logs` | 审计日志 |
| GET | `/api/audit-summary` | 审计汇总 |
| GET | `/api/token-usage` | Token 明细与汇总 |

### 5.4 扩展、MCP、沙箱、数据库、图谱

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/plugins/tools` | 插件工具列表 |
| POST | `/api/plugins/:id/test` | 插件工具测试 |
| GET | `/api/mcp/status` | MCP 状态 |
| POST | `/api/mcp/:id/connect`、`disconnect`、`call` | MCP 连接/断开/调用 |
| POST | `/api/mcp/reconnect` | 重连全部 |
| GET/POST | `/api/sandbox/status`、`toggle`、`commit`、`discard` | 沙箱 |
| GET | `/api/db/tables`、`/api/db/table/:name` | 数据库浏览 |
| POST | `/api/db/query`、`/api/db/maintenance` | 只读 SQL / 维护 |
| GET | `/api/db/backup` | 备份下载 |
| GET/POST/DELETE | `/api/graph`、`/api/graph/entities`、`/api/graph/edges` | 知识图谱 |
| POST | `/api/graph/rebuild` | 从知识库重建 |

### 5.5 自主性 L2 / L3

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/autonomy/status` | 巡检/队列/画像/策略/目标状态 |
| POST | `/api/autonomy/patrol` | 立即巡检 |
| POST | `/api/autonomy/patrol/config` | 巡检开关/间隔 |
| GET | `/api/profile`、`POST /api/profile/rebuild` | 用户画像 |
| GET | `/api/strategies`、`DELETE /api/strategies/:id` | 策略库 |
| GET | `/api/proactive/pending` | 待处理建议 |
| POST | `/api/proactive/:id/resolve` | 采纳/忽略建议 |
| GET | `/api/autonomous/tasks` | 自主任务列表 |
| POST | `/api/autonomous/queue` | 任务入队（支持每天/每周/每月循环、重试、超时） |
| POST | `/api/autonomous/tasks/:id/retry` | 重新入队 |
| POST | `/api/autonomous/tasks/:id/abandon` | 放弃任务 |
| GET/POST | `/api/goals`；`PATCH/DELETE /api/goals/:id` | 长程目标 |
| POST | `/api/goals/:id/review` | 目标验收 |

### 5.6 设置、用量、工具页、家庭成员、笔记、编码、工作区

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/settings` | 工具开关/偏好/计费/模型分工/家庭状态/版本 |
| POST | `/api/settings/tools` | 保存工具开关 |
| POST | `/api/settings/preferences` | 保存偏好 |
| POST | `/api/settings/pricing` | 保存计费单价 |
| POST | `/api/settings/family` | 启用/修改家庭访问口令 |
| POST | `/api/settings/model-routes` | 保存五类模型分工 |
| GET | `/api/family/status` | 家庭访问状态 |
| POST | `/api/family/login` / `logout` | 家庭口令登录/退出 |
| GET/POST | `/api/notes` | 笔记列表/搜索、新建 |
| GET/PATCH/DELETE | `/api/notes/:id` | 笔记详情、修改、删除 |
| GET | `/api/notes/export` | 导出笔记 JSON |
| POST | `/api/notes/import` | 导入笔记 JSON |
| GET/POST | `/api/journal` | AI 工作日志查询/写入 |
| GET | `/api/coding/agents` | 检测本机编码 CLI |
| POST | `/api/coding/run` | 启动编码任务 |
| GET | `/api/coding/runs` | 最近编码任务 |
| GET | `/api/coding/runs/:id/events` | 编码任务 SSE 输出 |
| POST | `/api/coding/runs/:id/stop` | 停止编码任务 |
| GET | `/api/notifications` | 通知列表与未读数 |
| POST | `/api/notifications/:id/read` | 单条已读 |
| POST | `/api/notifications/read-all` / `read-session` | 全部已读 / 按群已读 |
| GET | `/api/desktop/notifications?after=` | 桌面壳轮询新通知与未读数（仅本机） |
| GET | `/api/desktop/selection?after=` | 工作台轮询全局划词（仅本机） |
| POST | `/api/desktop/selection` | 启动器投递全局划词（仅本机） |
| POST | `/api/desktop/selection/ack` | 工作台确认已展示并清空划词 |
| GET/POST | `/api/backups` | 备份列表 / 创建备份 |
| DELETE | `/api/backups/:id` | 删除备份 |
| POST | `/api/backups/:id/restore` | 恢复备份（需确认） |
| POST | `/api/settings/backup` | 自动备份开关 |
| GET | `/api/usage/overview?days=` | 用量总览、每日数据、模型分布 |
| GET | `/api/changelog` | 解析更新日志 |
| POST | `/api/tools/translate` | 翻译 |
| POST | `/api/tools/text-action` | 划词解释 / 总结（快速模型） |
| POST | `/api/tools/screenshot` | 截屏 |
| GET | `/api/tools/screenshots` | 最近截图 |
| GET | `/api/screenshot/:filename` | 只读访问截图 |
| GET | `/api/workspace/files` | 工作区文本文件列表 |
| GET | `/api/workspace/file` | 读取工作区文本文件 |

### 5.7 开放 API 与 Webhook

| 方法 | 路径 | 说明 |
|------|------|------|
| GET/POST | `/api/open/config` | 开放配置（开关 / Key / Webhook），GET 返回完整 Key |
| POST | `/api/open/webhook/test` | 发送测试事件 |
| GET | `/api/open/health` | 开放接口健康检查 |
| POST | `/api/open/chat` | 外部发起对话（可指定群会话） |
| POST | `/api/open/tasks` | 创建后台任务 |
| GET | `/api/open/tasks/:id` | 查询后台任务 |

除 `/api/open/config` 与 `/api/open/webhook/test` 外，均需 `X-API-Key`。

### 5.8 后台与认证

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/admin/auth` | 后台鉴权状态 |
| POST | `/api/admin/login`、`logout` | 登录/退出 |
| GET | `/api/admin/overview` | 概览 |
| GET/DELETE | `/api/admin/sessions` | 会话管理 |
| GET | `/api/admin/messages`、`/api/admin/intents` | 消息/意图查询 |
| GET | `/api/admin/export` | 导出聊天记录 |
| GET | `/api/check-login` | 提供商连通性 |
| POST | `/api/save-env-config` | 保存 .env 配置 |
| GET/POST | `/api/config-permission` | 权限档位 |

---

## 六、配置项

配置文件：`.env`（优先级高于系统环境变量）。示例见 `.env.example`。

### 6.1 模型提供商

| 键 | 说明 |
|----|------|
| `AI_PROVIDER` | 当前提供商 key，如 `siliconflow` |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` | OpenAI 兼容配置 |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` / `ANTHROPIC_MODEL` | Claude 配置 |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` / `OLLAMA_TIMEOUT_MS` | 本地 Ollama |
| `PROVIDER_<KEY>_API_KEY` / `_BASE_URL` / `_MODEL` | 单个提供商覆盖 |

### 6.2 工具与执行

| 键 | 默认 | 说明 |
|----|------|------|
| `FILE_TOOLS_ENABLED` | true | 文件工具 |
| `TERMINAL_ENABLED` | true | 终端命令 |
| `GUI_ENABLED` | true | 桌面 GUI |
| `SCREEN_CAPTURE_ENABLED` | true | 屏幕截图 |
| `SANDBOX_MODE` | 0 | 文件沙箱 |
| `CONFIG_PERMISSION_LEVEL` | 3 | 权限档位 1/2/3 |
| `MAX_AGENTS` | 3 | 群聊最多发言成员 |
| `AI_INTELLIGENCE_LEVEL` | 3 | 智力档位 1-6 |
| `AGENT_WORKSPACE` | `workspace` | 工作区 |
| `AGENT_ALLOWED_ROOT` | 空 | 额外允许的根目录 |

### 6.3 自主性

| 键 | 默认 | 说明 |
|----|------|------|
| `PROACTIVE_PATROL_MINUTES` | 30 | 后台巡检间隔 |
| `DEFAULT_CHAT_MODE` | group | 新对话默认模式 |
| `DEFAULT_WORK_DIR` | workspace | 新对话默认工作目录 |

### 6.4 用量计费

| 键 | 默认 | 说明 |
|----|------|------|
| `USAGE_INPUT_PRICE` | 0 | 输入单价/百万 Token |
| `USAGE_OUTPUT_PRICE` | 0 | 输出单价/百万 Token |
| `USAGE_CACHED_PRICE` | 0 | 缓存命中单价/百万 Token |
| `USAGE_CURRENCY` | CNY | CNY / USD |

### 6.5 服务与安全

| 键 | 默认 | 说明 |
|----|------|------|
| `PORT` | 3000 | 监听端口 |
| `BIND_HOST` | 127.0.0.1 | 绑定地址 |
| `ADMIN_AUTH` | auto | 后台鉴权策略 |
| `ADMIN_PASSWORD` | 空 | 后台密码 |
| `FAMILY_ACCESS_PASSWORD` | 空 | 家庭访问口令；留空关闭，设置后数据接口需登录 |
| `LOG_ENCODING` | auto | 日志编码 |
| `INTENT_TIMEOUT_MS` | 依提供商 | 意图识别超时 |
| `MAX_MODEL_ATTEMPTS` | 1 | 模型重试次数 |

---

## 七、安全与护栏

**权限**

- 三档权限：只读 / 变更确认 / 极速自动
- 档位 2 写操作必须经前端确认卡
- 工具开关与权限独立：关闭的工具既不下发也不执行

**文件**

- 默认只允许项目根与 `workspace/`
- `../` 越界与工作区外绝对路径拦截
- 文件级沙箱：写入先落 `data/sandbox`，确认后提交
- `@` 引用只允许工作区文本文件，单文件 ≤512KB

**命令**

- 危险命令黑名单：格式化、关机、注册表删除、用户管理、磁盘操作等
- cwd 限制在工作区/项目根

**桌面**

- GUI 工具需要桌面 GUI 开关
- 截图需要屏幕截图开关
- 截图接口只读、路径越界校验

**家庭访问**

- 单口令 + HMAC-SHA256 签名 Cookie，不是账号体系
- 未登录时数据接口统一 401，前端先校验再加载
- 登录失败限流：10 分钟 8 次
- `HttpOnly` + `SameSite=Lax`，默认 30 天
- 默认只监听 `127.0.0.1`；开放局域网需显式改 `BIND_HOST`

**笔记可见性**

- `private` 笔记只允许人类读取，AI 工具强制拒绝
- AI 搜索强制过滤 `private`
- AI 只能写入 `shared` / `agent`

**群成员权限**

- 每个群独立成员列表，未启用的成员不参与群聊
- 关闭发言：不进入意图候选与发言队列
- 关闭工具：不下发任何工具定义
- 关闭写文件 / 命令 / 笔记：`executeTool` 层直接拒绝
- 待确认成员（`enabled=0`）不参与群聊
- 群人数上限与扩容仅本地生效，不涉及付费

**备份与恢复**

- 备份目录固定为 `data/backups/`，接口对备份 ID 做路径越界校验
- 恢复必须显式 `confirm: true`，且恢复前自动生成安全备份
- 工作区复制跳过符号链接，限制 20 层深度与 300MB
- 不使用 `fs.cpSync`（当前运行时会导致进程崩溃），改为自实现递归复制

**网络与密钥**

- API Key 在接口中脱敏返回
- `.env` 强制覆盖系统同名环境变量
- MCP 子进程连接池，退出时清理

**边界说明**

- 单机家庭成员版：单口令，无账号 / 租户隔离
- 沙箱是文件级，不是虚拟机/容器
- 编码搭档以当前用户权限运行本机 CLI，命令模板由使用者自行填写
- 桌面自动化会真实操作鼠标键盘，需谨慎授权

---

## 八、验收与实测

### 8.1 基础健康

- `GET /api/health` → 200
- 当前提供商 SiliconFlow，默认模型 `deepseek-ai/DeepSeek-V3`
- 当前版本 `v1.9`

### 8.2 主界面

- 906px 视口侧栏可见，无横向滚动
- 助手库、多标签、历史会话、@ 引用均通过
- 标签刷新恢复，已删除会话自动重置

### 8.3 设置与用量

- 设置中心 5+1 页签正常
- 44 个提供商、当前提供商回填、模型 chips 正常
- 工具开关：关闭终端后 `execute_command` 不再下发，强行调用被拦截
- 用量：35 次请求 / 133,592 Token / 2 个模型
- 计费实测：输入 2、输出 8、缓存 0.5 元/百万 → 0.328714 元

### 8.4 自主性

- 后台巡检：扫描 30 个会话，新增 9 条建议
- 队列任务：自动规划 2 步并真实落盘
- 失败重规划：强制第 1 步失败后重规划成功，`replanCount=1`
- 策略：高分任务沉淀，后续同类任务命中
- 长程目标：关联任务完成后自动验收通过

### 8.5 工具页

- 翻译：`Hello, world.` → `你好，世界。`
- 知识库：12 条、12 条启用、累计命中 8、FTS5 已启用；搜索“配置”命中 3 条
- 定时任务：46 个会话可选；未来 1 小时任务保持 `queued`
- 截图：1707×1067 PNG，图片可正常访问
- 更新日志：19 条记录，v0.1 → v1.9（含计划）

### 8.6 前端质量

- 浏览器控制台无页面自身 JS 报错
- 906px 无横向溢出
- 主要弹窗支持 Escape 关闭

### 8.7 家庭成员版（v1.9）

- 家庭访问启用后 `/api/sessions` 返回 401 `FAMILY_AUTH_REQUIRED`
- 正确口令登录后 Cookie 生效，`/api/sessions` 返回 46 条
- 关闭口令后恢复免登录
- 编码搭档：`powershell -NoProfile -Command "Write-Output {goal}"` 实测输出 `code-ok`，`status=completed`、`exit_code=0`
- 自动生成笔记「编码任务：code-ok」（`visibility=agent`），`coding_runs.note_id` 已关联
- AI 工作日志写入 `coding_run` 事件
- 笔记 CRUD 全通过；AI 读取 `private` 笔记被拒绝
- 模型分工五类保存后可从 `/api/settings` 读回

### 8.8 划词助手（v2.0）

- 在工具页内选中「原文不会写入会话历史。」，工具条 5 个按钮全部可见、命中测试通过
- 翻译返回英文译文，87 tokens
- 总结返回「原文不会保留在聊天记录中。」，48 tokens
- 存笔记预填标题、正文、状态正确，等待 3 秒未被已有笔记覆盖
- 浏览器控制台无页面自身脚本报错

### 8.9 任务可靠性（v2.1）

- 循环计算：每天 09:00 → 次日 09:00；每周一 09:00 → 下周一 09:00；每月 31 日在 11 月 30 日后跳到 12 月 31 日
- 循环任务成功：状态回到 `queued`，`due_at` 为下一周期，计划与步骤检查点归零
- 失败自动重试：`auto_retry=1`，`due_at` 设为退避时间；重试耗尽后循环任务仍安排下一次
- 一次性任务重试耗尽：保持 `paused`，不再被巡逻重复入队
- 服务重启：`running` → `queued`，`started_at` 清空，按检查点续跑
- 错过补跑：过期任务立即执行一次，随后跳到下一个未来周期
- API 实测：每天 23:59 入队，返回正确的下次执行时间与循环/重试/超时字段
- UI 实测：循环表单显隐正确，列表显示「每天 23:59」、重试次数，放弃操作正常
- 浏览器控制台无新增页面脚本错误

### 8.10 群聊管理（v2.2）

- 迁移：46 个历史群全部生成唯一群号，缺失数为 0；每群自动初始化 4 名启用成员
- API：创建测试群 → 群号生成 → 成员 4 人 → 关闭 `speak/tools` → 扩容 50 → 删除，全部成功
- 权限：`executeTool('write_file', …, { disabledTools: ['write_file'] })` 被拒绝，目标文件未生成
- UI：群号搜索命中；群资料正确回填；成员 4 行 × 5 个权限开关；关闭「命令」后切换页签仍保持
- UI：扩容 50、置顶保存并在列表显示、删除群后搜索不到，全部通过
- 浏览器控制台无页面脚本报错；测试群已清理

### 8.11 群聊入口与侧栏（v2.3）

- 侧栏常驻 47 个群，每项显示头像、群名、最后消息预览、时间
- 点击群项进群并高亮；「群聊 / 助手」切换正常
- 群号 27637301 搜索精确过滤为 1 项
- 「⋯」打开群管理三页签
- 新建群「侧栏UI测试」：只选 2 名成员，创建后侧栏显示「群号 91954395 · 2 人」并自动打开
- 「全部群聊」弹窗可搜到新群
- 浏览器控制台无页面自身脚本报错；测试群已清理

### 8.12 数据备份与通知（v2.4）

- 备份：创建含工作区的备份成功，`workspace=true`，服务未崩溃
- 恢复：测试笔记 → 备份 → 删除 → 恢复 → 笔记重新出现，随后清理
- 通知：编码任务完成自动生成通知，未读 1；标记已读后未读 0
- UI：🔔 徽标、通知面板、跳转编码页、已读持久化正常
- UI：数据页可创建备份、删除备份，列表与大小显示正常
- 浏览器控制台无新增页面脚本错误
- 测试数据已清理，保留 1 份干净备份

### 8.13 群聊体验 + 插件编排 + 开放 API（v2.5）

- 开放 API：`keyLen=48`；`health` 返回 `True version=2.5`；`chat=OK`；未读 `1 → afterRead=0`
- 消息搜索：`search=2`，首条命中正确，点击可定位
- 群文件：上传 `hello.txt`（16B）→ 下载内容 `hello group file` → 删除后列表为 0
- 开放任务：创建返回 `status=queued`，`GET /api/open/tasks/:id` 可查询，测试任务已放弃
- @ 成员：SSE 收到 `mentions=[全栈开发 · 阿码]`、`speakers=[全栈开发 · 阿码]`、`done=true`
- Webhook：`POST /hook` 收到 `{"event":"webhook.test",...}`
- 插件：表单保存后测试调用返回 `success=True stdout=plugin-ok`
- 浏览器 7/7 通过：未读徽标、@ 群成员菜单、消息搜索高亮、群文件页、插件建/测/删、开放配置页、控制台 0 报错
- 测试数据已清理（测试群、测试任务、群文件目录、Webhook 配置），保留 1 份含工作区的干净备份；当前 46 个群

### 8.14 群聊深化（v2.6）

- 数据：`messages.reply_to_id`、`sessions.speech_mode` 迁移成功，老群默认 `standard`
- 引用回复：第二条消息落库 `reply_to_id` 指向第一条用户消息，读回 `replied=1`
- 群公告：PATCH 后立即读回公告文本；浏览器横幅展示、收起、展开正常
- 发言频率：`quiet` 无 @ 时 `speakers=1`；`mention` 无 @ 时 `speakers=1`；面板标准⇄克制保存成功
- 浏览器：引用块与点击高亮、回复条与取消、发言频率下拉全部通过，控制台 0 页面脚本报错
- 测试群与临时脚本已清理，`/api/health` 正常

### 8.15 最小桌面壳（v2.7）

- 编译：`csc` 编译 `Launcher.cs` 成功，生成 `智能Agent群.exe`
- 开机自启：`--enable-autostart` 写入值为 `"E:\智能体ai\智能Agent群.exe" --silent`；`--disable-autostart` 后清空
- 静默启动：托盘进程存活，后台服务返回 `version=2.7`，`logs/server.pid` 正确写入
- 通知链路：插入测试通知 → `/api/desktop/notifications` 返回 1 条 → 清理后 0 条
- 修复：Node 22 下 `--import app/instrument.mjs` 报 `ERR_MODULE_NOT_FOUND`，改为 `./app/instrument.mjs` 后启动正常
- 测试通知已清理，开机自启保持关闭

### 8.16 桌面壳增强（v2.8）

- 编译：`csc` 编译 `Launcher.cs` 成功，生成新的 `智能Agent群.exe`，启动后 `/api/health` 返回 200
- 全局快捷键：默认 `Ctrl+Alt+Space` 与 `Ctrl+Alt+X` 已被启动器占用；探测程序注册同一组合返回失败，注册未占用组合返回成功
- 快捷键配置：写入带 BOM 的 `data/desktop-shell.json`（F11 / F12）后重启，F11 / F12 被占用、旧默认组合释放；删除配置并重启后恢复默认
- 全局划词链路：`POST /api/desktop/selection` → `GET` 取回原文 → `ACK` 后返回 `null`
- 全局划词 UI：浏览器打开工作台后，预先投递的划词文本使顶部工具条自动出现，翻译 / 解释 / 总结 / 存笔记 / 复制五个按钮可见，位置居中
- 托盘角标：插入测试通知后托盘提示变为「运行中 · 1 条未读」，角标图标渲染为红底数字 `3`；删除通知后恢复为无未读
- 修复：配置文件带 UTF-8 BOM 时 JSON 解析失败，加载前剥离 BOM
- 前端脚本语法检查通过（1 个 script 块），测试通知与临时截图已清理

---

## 九、版本演进

| 版本 | 主题 |
|------|------|
| v0.1 | 地基版：可运行的多 Agent 群聊 |
| v0.2 | 后台管理恢复 |
| v0.3 | AI 神殿扩展：6 模式 + 知识库 + 自主性路线 |
| v0.4 | AI 提供商链路修复 |
| v0.5 | 知识库闭环 + 多模态 + 自主进化 |
| v0.6 | 交互完善：确认流 + 主动建议 + 工作流编辑器 |
| v0.7 | 模型能力识别 |
| v0.8 | 自主 Agent + Agent 市场 + REST API |
| v0.9 | 表演型执行修复：让每个模式真的干活 |
| v1.0 | 提示词约束 + 智力六档 + 审计日志 + Token 统计 |
| v1.1 | UI 重构 + 商店 + 知识图谱 + 记忆/学习修复 |
| v1.2 | MCP + 沙箱 + 数据库页 + 六档评分 + 知识库重构 |
| v1.3 | 插件声明式工具 + 断点续跑 + MCP 三传输 |
| v1.4 | 工作流真实化 + 主动服务修正 + GUI + 工作区 |
| v1.5 | L2/L3 收尾：巡检、画像、队列、重规划、策略、目标 |
| v1.6 | 主界面产品化：助手库 + 多标签 + @ 引用 |
| v1.7 | 设置中心 + 用量中心 |
| v1.8 | 工具页 + 应用内更新日志 |
| v1.9 | 家庭成员版：访问口令 + 编码搭档 + 笔记与工作日志 + 模型分工 |
| v2.0 | 页面内划词助手：翻译 / 解释 / 总结 / 存笔记 / 复制 |
| v2.1 | 任务可靠性 + 循环定时：每天/每周/每月、自动重试、超时、重启恢复、错过补跑 |
| v2.2 | 群聊管理：群资料、群号、独立 AI 成员、成员权限、置顶/免打扰/折叠、人数扩容 |
| v2.3 | 群聊入口重做：侧栏常驻群列表、新建群聊、最后消息预览、群号搜索 |
| v2.4 | 数据备份与恢复 + 站内通知：自动备份、一键恢复、未读徽标、任务完成提醒 |
| v2.5 | 群聊体验补齐（@ 成员、消息搜索、群文件、未读细化）+ 插件可视化编排 + 开放 API / Webhook |
| v2.6 | 群聊深化：引用回复、群公告置顶、成员发言频率控制 |
| v2.7 | 最小桌面壳：托盘常驻、开机自启、服务状态与系统通知 |
| v2.8 | 桌面壳增强：全局快捷键、全局划词、托盘未读角标 |

完整的新增/修复/验证记录见 `更新日志.md`。

---

## 十、已知限制与 v2.9 路线

### 10.1 已知限制

- 定时任务已支持循环；每次执行会重置同一行的计划与步骤记录，历史只保留在 AI 工作日志
- 循环任务同一时刻只执行一个，多个任务同时到点会排队
- 成本单价需手动填写；没有按模型自动价格表
- 历史 Token 记录没有缓存字段，缓存命中率从 v1.7 之后才开始积累
- 策略匹配基于关键词相似度，没有效果回归
- 系统通知使用托盘气泡，不写入 Windows 通知中心历史
- 开机自启写当前用户注册表，不涉及系统级服务
- 全局快捷键组合需包含 Ctrl / Alt / Shift 之一，不支持单独 Win 键组合
- 全局划词依赖前台应用响应 `Ctrl+C`；终端、部分游戏或不支持复制的控件无法捕获
- 全局划词结果只在本机的 AI 神殿页面展示，页面未打开时先暂存在服务内存
- 托盘角标依赖系统托盘显示图标；Windows 11 可能把图标收进溢出区
- 群号仅用于本地查找，不是网络群号，不能跨设备加群
- 群人数上限与扩容是本地交互，不涉及付费或外部成员
- 置顶、免打扰、折叠只影响群列表展示，不改变消息处理
- 群文件只支持单文件 ≤ 10MB，不支持批量上传与预览
- 消息搜索为关键词匹配，不做语义检索
- 引用回复只支持单层引用，不支持多层嵌套树展示
- 群公告每个群只有一条；收起状态只保存在当前浏览器
- 发言频率控制的是每轮发言人数，不是按时间间隔限流
- 开放 API 只有单个 Key，没有按调用方的权限与限流
- Webhook 只推送一次，不重试、不签名校验
- 备份恢复会覆盖当前数据库；恢复前会自动生成安全备份
- 工作区恢复是合并覆盖，不会删除当前多出来的文件
- 单机家庭成员版，没有账号 / 多租户
- 翻译已支持单独指定模型（模型分工），但尚未做按语言自动路由
- 截图 UI 只提供全屏截图，窗口截图能力在工具层存在但未暴露
- 知识库导入单次最多 500 条
- 编码搭档依赖本机已安装的 CLI；未安装时只能使用自定义命令
- 编码任务不自动做代码审查，只负责执行与记录
- Groq / 腾讯 TokenHub / 商汤 Key 仍可能失效，需在设置中心更新

### 10.2 v2.9 路线

- 策略效果回归：统计复用成功率，淘汰低效策略
- 群聊再深化：消息撤回、@全体成员、群成员发言顺序自定义

---

## 十一、文件地图

```
E:\智能体ai\
├── 启动.bat / 停止.bat
├── 智能Agent群.exe                 # 托盘宿主（Launcher.cs 编译）
├── Launcher.cs                     # 托盘 / 自启 / 系统通知 / 快捷键 / 角标源码
├── 使用说明.txt                    # 用户使用说明
├── 更新日志.md                     # v0.1 → v2.8
├── AI神殿项目全景文档.md            # 历史全景（转型前）
├── AI神殿项目全景文档-转型后.md     # 本文档
├── .env / .env.example
├── workspace\                      # AI 产出默认目录
│   ├── screenshots\                # 截图
│   └── group-files\                # 群文件（按群 ID 分目录）
├── data\chat.db                    # SQLite
├── data\backups\                   # 自动 / 手动备份
├── data\desktop-shell.json         # 全局快捷键配置（首次修改后生成）
├── logs\server.log / server.err.log
├── runtime\node.exe
└── app\
    ├── server.mjs                  # 主服务
    ├── config\providers.json       # 44 个提供商预设
    ├── dist\
    │   ├── index.html              # 主工作台
    │   └── admin.html              # 后台管理
    └── engine\
        ├── providers.mjs           # 流式模型 + 工具循环
        ├── tools.mjs               # 工具 + 权限 + 沙箱 + GUI
        ├── autonomy-service.mjs    # L2/L3
        ├── proactive-service.mjs   # 主动服务/记忆/学习
        ├── providers-service.mjs   # 提供商配置
        ├── prompt-config.mjs       # 提示词/智力/审计
        ├── model-capabilities.mjs  # 模型能力
        ├── store-service.mjs       # 商店
        ├── plugin-tools.mjs        # 插件工具
        ├── mcp-client.mjs          # MCP
        ├── uia.mjs / screen.mjs    # 桌面自动化
        └── db-migration.mjs        # 数据库迁移
```

---

## 十二、常见问题与排错要点

1. **模型不回复 / 401**
   - 先看 `/api/health` 的 providerKey、baseUrl、hasApiKey
   - 到设置中心 → 模型服务确认 Key 与 Base URL
   - 不要只看 `.env`，系统环境变量可能覆盖（当前已改为 `.env` 优先）

2. **工具没有执行**
   - 检查设置中心 → 工具开关
   - 检查权限档位是否为 1（只读）
   - 检查审计日志中的 `pending_confirmation` / `failed`

3. **工具返回“已在设置中心关闭”**
   - 对应工具开关被关闭；开启后下一条消息生效

4. **标签页打开报“读取会话失败”**
   - 会话已被删除；v1.7 起会自动重置为“新对话”
   - 若仍出现，清理 localStorage 中的 `aitemple_tabs_v2`

5. **截图失败**
   - 设置中心开启桌面 GUI 与屏幕截图
   - 检查 `workspace/screenshots` 是否可写
   - 远程桌面/锁屏状态下截图可能失败

6. **定时任务不执行**
   - 服务需保持运行
   - `due_at` 必须到达；队列每 30 秒检查一次
   - 查看任务状态是否为 `paused`，可手动重试

7. **成本显示 0**
   - 用量中心填写输入/输出/缓存单价
   - 单价是每百万 Token，修改后历史数据会按当前单价重算

8. **后台打不开**
   - 默认 `127.0.0.1` 仅本机可访问
   - 对外开放时检查 `ADMIN_PASSWORD`
   - 查看 `logs/server.err.log`

9. **开放 API 返回 401 / 403**
   - 401：缺少或错误的 `X-API-Key`，到设置中心 → 开放复制当前 Key
   - 403：开放接口总开关未开启
   - 外部调用需能访问本机端口，默认只监听本机

10. **群文件上传失败**
    - 单文件上限 10MB；超过需先压缩或拆分
    - 检查 `workspace/group-files` 是否可写
    - 文件名会做安全化处理，重名时保留原文件并新增记录

11. **托盘图标在但服务打不开**
    - 右键托盘 → 查看运行日志，先看 `logs/server.err.log`
    - 托盘每 20 秒刷新状态，状态异常会弹通知
    - 端口被占用时关闭占用程序，或修改 `.env` 的 `PORT`

12. **开机自启没生效**
    - 托盘菜单确认「开机自启」已勾选
    - 检查注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 的 `AITemple`
    - 也可运行 `智能Agent群.exe --enable-autostart` 重新写入

13. **收不到系统通知**
    - 检查 Windows 通知与专注助手是否拦截
    - 通知每 20 秒拉取一次，稍等再试
    - 本机接口 `/api/desktop/notifications` 应能返回新通知与 `unreadCount`

14. **全局快捷键没反应**
    - 右键托盘 → 运行状态，确认显示的快捷键组合
    - 打开「全局快捷键设置…」重新录制，避免与其他软件冲突
    - 组合键必须包含 Ctrl / Alt / Shift 之一

15. **全局划词没反应**
    - 先确认目标应用支持选中文本后 `Ctrl+C` 复制
    - 确认托盘显示「运行中」且工作台页面已在本机打开
    - 页面每 2 秒轮询一次，稍等再看顶部工具条

---

## 十三、附录

### 附录 A：快速启动

```bat
:: 启动
启动.bat

:: 或手动
runtime\node.exe app\server.mjs
```

访问：

- 主界面：`http://127.0.0.1:3000`
- 后台：`http://127.0.0.1:3000/admin.html`

### 附录 B：关键路径

| 用途 | 路径 |
|------|------|
| 数据库 | `data/chat.db` |
| 工作区 | `workspace/` |
| 截图 | `workspace/screenshots/` |
| 日志 | `logs/server.log`、`logs/server.err.log` |
| 提供商预设 | `app/config/providers.json` |
| 更新日志 | `更新日志.md` |
| 主界面 | `app/dist/index.html` |
| 后台 | `app/dist/admin.html` |

### 附录 C：验收清单

- [ ] `/api/health` 返回 200
- [ ] 主界面助手库、标签、@ 引用正常
- [ ] 设置中心 6 个页签正常
- [ ] 用量中心 8 张卡、热力图、模型表正常
- [ ] 工具页 6 个工具可用（翻译 / 知识库 / 定时任务 / 截图 / 编码搭档 / 笔记）
- [ ] 家庭访问启用后未登录返回 401，登录后可正常访问
- [ ] 笔记三种可见性生效，AI 读不到 `private`
- [ ] 编码搭档可运行并自动生成笔记与工作日志
- [ ] 更新日志显示 v0.1 → v2.8
- [ ] 后台 12 个模块可访问
- [ ] 审计日志、Token 统计有数据
- [ ] 自主性巡检可手动触发
- [ ] 定时任务可循环入队、失败重试、超时暂停、重启恢复
- [ ] 群资料可保存，群号可搜索，成员权限可开关并真实生效
- [ ] 置顶 / 免打扰 / 折叠 / 扩容 / 删除群聊正常
- [ ] 侧栏常驻群列表可进群、可搜索、可新建群聊
- [ ] 数据页可备份、恢复、删除备份
- [ ] 通知徽标与通知面板正常
- [ ] 群内 @ AI 成员会强制该成员发言
- [ ] 消息搜索可命中并跳转高亮
- [ ] 群文件可上传、下载、删除
- [ ] 侧栏群未读徽标在打开群后清零
- [ ] 插件可在表单中新建并测试通过
- [ ] 开放接口需带 `X-API-Key`，关闭总开关后返回 403
- [ ] Webhook 测试事件可收到
- [ ] 消息可引用回复，引用块可定位原消息
- [ ] 群公告可在聊天顶部展示并收起 / 展开
- [ ] 成员发言频率四档可切换并保存
- [ ] 双击 `智能Agent群.exe` 托盘常驻并可打开工作台
- [ ] 托盘「开机自启」可勾选 / 取消，注册表值正确
- [ ] 后台任务完成时右下角收到系统通知
- [ ] `Ctrl+Alt+Space` 可唤起或最小化工作台
- [ ] 托盘「全局快捷键设置…」可重新录制并持久化
- [ ] 任意应用选中文本后 `Ctrl+Alt+X`，工作台顶部出现划词工具条
- [ ] 有未读通知时托盘图标显示红色数字角标，提示文字含未读数

---

*本文档描述转型后 v2.8 的完整状态；逐版变更见 `更新日志.md`，转型前历史见 `AI神殿项目全景文档.md`。*
