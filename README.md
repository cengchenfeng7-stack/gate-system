# AI 神殿 · 智能 Agent 群

> 多智能体协作平台 —— 群聊编排、知识记忆、万神殿生态、自主任务调度

一个运行在本地的多智能体协作后端与前端。核心能力：群聊多 Agent 编排、十大境界知识记忆体系、万神殿多智能体生态、自主任务队列与目标管理、编码搭档、笔记与工作日志、桌面壳集成。

---

## 快速开始

### 环境要求

| 项目 | 版本 |
|------|------|
| Node.js | ≥ 20（推荐 22/24 LTS） |
| npm | ≥ 10 |
| SQLite | 由 `better-sqlite3` 内置，无需单独安装 |

### 安装与运行

```bash
# 1. 安装后端依赖
cd app
npm install

# 2. 配置环境变量
cp .env.example .env
#    编辑 .env，填入模型提供商 API Key 等

# 3. 启动
npm start
```

服务启动后访问 `http://localhost:3000`。

### Windows 一键启动

```bat
启动.bat          :: 启动服务
停止.bat          :: 停止服务
```

### 安装前端依赖（可选）

```bash
cd web
npm install
npm run dev      :: 开发模式
npm run build    :: 构建到 app/dist
```

构建后服务端会自动托管 `app/dist`，单端口即可访问前后端。

---

## 架构

```
.
├── app/                    # 服务端（Node.js + Express + SQLite）
│   ├── server.mjs          # 主入口：数据库、路由注册、生命周期
│   ├── config/             # 配置单一真源
│   │   ├── constants.mjs        # 版本、默认值、错误码、境界阈值
│   │   ├── realms.mjs           # 十大境界完整配置
│   │   ├── feature-flags.mjs    # 功能开关
│   │   └── auth-core.mjs        # 认证原语（无依赖，被多方引用）
│   │
│   ├── middleware/         # 横切关注点
│   │   ├── auth.mjs             # 家庭口令 / 管理后台 / 开放 API Key
│   │   ├── validation.mjs       # 参数校验（数组 + 对象双写法）
│   │   ├── error.mjs            # 全局错误处理、404、异步包装
│   │   ├── oracle-filter.mjs    # 唯一真理协议：知识污染审查
│   │   └── resource-guard.mjs   # 资源配额守卫
│   │
│   ├── routes/             # 路由层（注册 + handler 分离）
│   │   ├── sessions.mjs   ├── chat.mjs      ├── memory.mjs
│   │   ├── autonomy.mjs   ├── pantheon.mjs ├── tools.mjs
│   │   ├── admin.mjs
│   │   └── */handlers.mjs # 各域业务实现
│   │
│   └── engine/             # 引擎层
│       ├── providers.mjs          # OpenAI / Claude / Ollama 流式接入
│       ├── tools.mjs              # 工具定义与执行
│       ├── coding.mjs             # 编码代理探测与命令构造
│       ├── notes.mjs              # 笔记服务
│       ├── journal.mjs            # 工作日志服务
│       ├── store-service.mjs      # 技能 / 插件 / MCP 商店
│       ├── model-capabilities.mjs # 模型能力分类
│       ├── db-migration.mjs       # 幂等数据库迁移
│       └── uia.mjs                # Windows UI Automation
│
├── web/                    # 前端（Vite + React + Tailwind）
├── docs/design/2024/       # 设计文档
└── workspace/              # Agent 工作目录
```

### 分层约定

```
请求 → 中间件链 → 路由注册 → handler → 引擎服务 → SQLite
         ↓
    auth → validation → resource-guard → oracle-filter
```

- **配置层**（`config/`）：纯常量与纯函数，零依赖
- **中间件层**（`middleware/`）：通过**依赖注入**获取服务端能力，避免循环依赖
- **路由层**（`routes/*.mjs`）：只做路径绑定与中间件编排
- **handler 层**（`routes/*/handlers.mjs`）：业务实现
- **引擎层**（`engine/`）：可独立测试的能力单元

---

## 核心功能

### 一、多智能体群聊

- 群聊 / 私聊 / 工作流 / 主动服务 / 连续对话 / 创意协作 / 数据分析 / 自主代理 / 跨平台 共 9 种模式
- 意图识别（本地规则 + 模型增强双通道）
- SSE 流式响应
- 消息撤回 / 删除、引用回复、群文件、群公告
- 成员权限与发言频率控制

### 二、十大境界记忆体系

| 境界 | 名称 | 核心能力 | 解锁条件 |
|------|------|----------|----------|
| L1 | 混沌之海 | 原始知识摄入 | — |
| L2 | 原初石板 | 粗略精炼 | 实体抽取完成 |
| L3 | 秩序圣殿 | 本我神职熔炉 | 知识条目锻造 |
| L4 | 神魂交织 | 跨模型融合 | 双模型图谱合并 |
| L5 | 神职之巅 | 专业极致推演 | — |
| L6 | 神王之境 | 点石成金 | 领域边界重塑 |
| L7 | 超凡入圣 | **阿卡西库写入** | 通过 L6→L7 试炼 |
| L8 | 万法归宗 | **统一核心立法** | 通过 L7→L8 试炼 |
| L9 | 已知宇宙之主 | 全知全能自检 | 四项门槛全达标 |
| L10 | 无限未知之路 | 未知探索 | — |

权限门禁在服务端强制校验，未达境界的写入请求返回 `403 PERMISSION_DENIED`。

**瓶颈试炼**：逐境界申请，不可跳跃；自动化试炼直接通过，其余需人工裁决。

```bash
# 完整链路示例
POST /api/memory/raw/ingest          # L1 摄入
POST /api/memory/raw/refine          # L1→L2 精炼
POST /api/memory/knowledge/forge     # L2→L3 熔炉
POST /api/memory/knowledge/fuse      # L3→L4 融合
POST /api/memory/core/transmute      # L5→L6 点石成金
POST /api/memory/trial/apply         # 申请瓶颈试炼
POST /api/memory/trial/judge/:id     # 人工裁决
POST /api/memory/core/akashic/write  # L7+ 阿卡西写入
GET  /api/memory/lord/omniscient/check  # L9 全知自检
```

### 三、万神殿生态

神祇点化 / 模块装配 / 破壁飞升 / 自我命名天梯 / 资源分配器 / 圆桌议会 / 现实干预（神之手）/ 星语系统 / 世界树备份。

### 四、自主性

- 定时巡检与主动服务
- 长程目标管理与验收
- 策略库（自动积累、评估、剪枝）
- 任务队列（优先级、循环调度、重启恢复）

### 五、编码搭档

内置 Claude Code / Codex / Gemini / Aider / Cursor / OpenCode 探测与调度，SSE 实时输出，运行结束自动生成笔记。

### 六、唯一真理协议

知识写入时自动分类（技术 / 历史 / 灰色 / 剧毒），剧毒直接封禁，灰色地带进入隔离区等待人工仲裁。

---

## 安全机制

| 机制 | 说明 |
|------|------|
| 家庭访问口令 | 局域网暴露时可用，Cookie 会话令牌 |
| 管理后台鉴权 | 非回环绑定时强制启用，HttpOnly + SameSite=Strict |
| 开放 API Key | 独立权限位（`read` / `chat` / `tasks` / `admin`），时间安全比较 |
| 路径穿越防护 | 所有文件读写限定在工作区内，挡 `..` 与绝对路径 |
| 知识污染隔离 | 剧毒封禁 + 灰色仲裁 |
| 资源配额 | CPU / 内存 / 磁盘 / 并发任务上限 |

---

## API 概览

| 分组 | 前缀 | 说明 |
|------|------|------|
| 基础 | `/api/health` `/api/provider` `/api/models` | 健康检查、提供商、模型列表 |
| 会话 | `/api/sessions/*` `/api/messages/*` | 会话、消息、群成员、群文件 |
| 智能体 | `/api/agents` | 成员增删改查 |
| 知识库 | `/api/faq/*` | FAQ CRUD、导入导出、FTS 重建 |
| 记忆 | `/api/memory/*` | 十大境界全链路 |
| 自主性 | `/api/autonomy/*` `/api/goals` `/api/strategies` | 巡检、目标、策略、任务队列 |
| 万神殿 | `/api/pantheon/*` | 神祇、议会、现实干预、星语 |
| 工具 | `/api/tools/*` `/api/coding/*` `/api/notes` `/api/journal` | 翻译、划词、截图、编码、笔记 |
| 设置 | `/api/settings/*` | 工具开关、偏好、计费、模型分工、备份 |
| 管理后台 | `/api/admin/*` | 统计、会话管理、消息检索、导出 |
| 开放接口 | `/api/open/*` | 对外 API Key 调用入口 |
| 商店 | `/api/store/*` | 技能 / 插件 / MCP 管理 |

---

## 开发

```bash
cd app
npm run dev      # --watch 热重载
npm run check    # 语法检查
```

端到端验证：

```bash
node e2e-memory.mjs      # 记忆链路 15 项断言
```

---

## 配置

复制 `.env.example` 为 `.env`，常用项：

| 变量 | 默认 | 说明 |
|------|------|------|
| `PORT` | 3000 | 服务端口 |
| `BIND_HOST` | 127.0.0.1 | 绑定地址（局域网访问改 0.0.0.0） |
| `ADMIN_AUTH` | auto | 管理鉴权策略 |
| `ADMIN_PASSWORD` | 自动生成 | 管理密码 |
| `FAMILY_ACCESS_PASSWORD` | 空 | 家庭访问口令 |
| `AI_PROVIDER` | ollama | `ollama` / `direct` |
| `OLLAMA_BASE_URL` | http://localhost:11434 | Ollama 地址 |
| `MAX_AGENTS` | 3 | 群聊最大 Agent 数 |
| `SANDBOX_MODE` | 0 | 沙箱模式 |
| `FILE_TOOLS_ENABLED` | 1 | 文件工具开关 |

---

## 许可证

私有项目，保留所有权利。