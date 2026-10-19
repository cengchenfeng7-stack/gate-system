import { runDatabaseMigrations } from "./engine/db-migration.mjs";
import { registerProviderEndpoints, resolveProvider } from "./engine/providers-service.mjs";
import { executeTool, checkPermission, toolPairPreservingSlice, getTruncatedSummary, TOOLS_OPENAI, TOOLS_CLAUDE } from "./engine/tools.mjs";
import { runAgentTurnLoop } from "./engine/providers.mjs";
import { classifyModel } from "./engine/model-capabilities.mjs";
import { PromptConfigStore, AuditLogger, INTELLIGENCE_LEVELS, getIntelligenceLevel } from "./engine/prompt-config.mjs";
import { registerStoreEndpoints, getEnabledStorePrompt } from "./engine/store-service.mjs";
import { HierarchicalMemory, AutonomousLearningEngine } from "./engine/proactive-service.mjs";
import { UserProfileService, ProactivePatrol, StrategyLibrary, TaskQueueService, GoalService, computeNextRun } from "./engine/autonomy-service.mjs";

// 简单的 JSON 安全解析
function safeJson(value, fallback) {
  try {
    const parsed = JSON.parse(value);
    return parsed === null || parsed === undefined ? fallback : parsed;
  } catch {
    return fallback;
  }
}
import { McpManager } from "./engine/mcp-client.mjs";
import { buildPluginTools, callPluginTool } from "./engine/plugin-tools.mjs";
// server/index.ts
import express from "express";
import fs2 from "fs";
import path2 from "path";
import crypto from "crypto";
import { spawn, spawnSync } from "child_process";
import { fileURLToPath as fileURLToPath2 } from "url";
import { unstable_v2_createSession, unstable_v2_authenticate } from "@tencent-ai/agent-sdk";
import { v4 as uuidv44 } from "uuid";

// server/db.ts
import Database from "better-sqlite3";
import path from "path";
import { fileURLToPath } from "url";
import fs from "fs";
var __filename = fileURLToPath(import.meta.url);
var __dirname = path.dirname(__filename);
var dbPath = path.join(__dirname, "..", "data", "chat.db");
var dataDir = path.dirname(dbPath);
if (!fs.existsSync(dataDir)) {
  fs.mkdirSync(dataDir, { recursive: true });
}
var db = new Database(dbPath);
runDatabaseMigrations(db);
// 提示词配置 / 审计日志 / 智力档位
var promptStore = new PromptConfigStore(db);
var auditLogger = new AuditLogger(db);
var hierarchicalMemory = new HierarchicalMemory(db);
var learningEngine = new AutonomousLearningEngine(db);
// L2 / L3 自主性服务（后台巡检、画像、策略库、任务队列、长程目标）
var userProfileService = new UserProfileService(db);
var strategyLibrary = new StrategyLibrary(db);
var proactivePatrol = new ProactivePatrol(db, {
  searchFaq: (q, l) => searchFaq(q, l, false),
  profileService: userProfileService,
  intervalMinutes: Number(process.env.PROACTIVE_PATROL_MINUTES) || 30
});
var goalService = new GoalService(db, { llm: callModelOnce });
var taskQueue = new TaskQueueService(db, {
  executeTask: runQueuedAutonomousTask,
  onTaskFinished: handleQueuedTaskFinished
});
var mcpManager = new McpManager(db);
var appIntelligenceLevel = Number(process.env.AI_INTELLIGENCE_LEVEL) || 3;
db.pragma("journal_mode = WAL");
db.pragma("wal_autocheckpoint = 50");
db.pragma("busy_timeout = 5000");
db.exec(`
  -- \u4F1A\u8BDD\u8868
  CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    model TEXT NOT NULL,
    sdk_session_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
  );

  -- \u6D88\u606F\u8868
  CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    model TEXT,
    created_at TEXT NOT NULL,
    tool_calls TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
  );

  -- \u4E3A\u4F1A\u8BDD ID \u521B\u5EFA\u7D22\u5F15
  CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id);
  CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at);

  -- \u7FA4\u6210\u5458\uFF08Agent\uFF09\u8868
  CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    avatar TEXT NOT NULL DEFAULT 'Bot',
    color TEXT NOT NULL DEFAULT '#0052d9',
    system_prompt TEXT NOT NULL,
    keywords TEXT,
    intents TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    builtin INTEGER NOT NULL DEFAULT 0,
    sort INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
  );

  -- FAQ \u77E5\u8BC6\u5E93\u8868
  CREATE TABLE IF NOT EXISTS faq (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '\u901A\u7528',
    keywords TEXT,
    hits INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
  );

  -- Agent \u4E0E\u4F1A\u8BDD\u7684 SDK session \u6620\u5C04\uFF08\u4FDD\u8BC1\u591A\u8F6E\u4E0A\u4E0B\u6587\uFF09
  CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    sdk_session_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (session_id, agent_id)
  );

  -- \u610F\u56FE\u8BC6\u522B\u65E5\u5FD7
  CREATE TABLE IF NOT EXISTS intent_logs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    message_id TEXT,
    query TEXT NOT NULL,
    intent TEXT NOT NULL,
    intent_name TEXT,
    source TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    agents TEXT,
    faq_id TEXT,
    reason TEXT,
    created_at TEXT NOT NULL
  );

  CREATE INDEX IF NOT EXISTS idx_intent_logs_session ON intent_logs(session_id);
  CREATE INDEX IF NOT EXISTS idx_faq_category ON faq(category);
`);
function ensureColumn(table, column, definition) {
  try {
    const tableInfo = db.prepare(`PRAGMA table_info(${table})`).all();
    const hasColumn = tableInfo.some((col) => col.name === column);
    if (!hasColumn) {
      db.exec(`ALTER TABLE ${table} ADD COLUMN ${column} ${definition}`);
      console.log(`[DB] Added ${column} column to ${table} table`);
    }
  } catch (e) {
  }
}
ensureColumn("sessions", "sdk_session_id", "TEXT");
ensureColumn("messages", "agent_id", "TEXT");
ensureColumn("messages", "agent_name", "TEXT");
ensureColumn("messages", "agent_color", "TEXT");
ensureColumn("messages", "intent", "TEXT");
ensureColumn("messages", "intent_source", "TEXT");
ensureColumn("messages", "intent_confidence", "REAL");
ensureColumn("messages", "faq_id", "TEXT");
ensureColumn("messages", "reply_source", "TEXT");
ensureColumn("faq", "last_hit_at", "TEXT");
function ensureMessageIndexes() {
  try {
    db.exec(`
      CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at);
      CREATE INDEX IF NOT EXISTS idx_intent_logs_created_at ON intent_logs(created_at);
    `);
  } catch (e) {
    console.warn("[DB] \u521B\u5EFA\u7D22\u5F15\u5931\u8D25(\u5FFD\u7565):", e?.message || e);
  }
}
function getAllSessions() {
  const stmt = db.prepare("SELECT * FROM sessions ORDER BY updated_at DESC");
  return stmt.all();
}
function getSession(id) {
  const stmt = db.prepare("SELECT * FROM sessions WHERE id = ?");
  return stmt.get(id);
}
function createSession(session) {
  const stmt = db.prepare(`
    INSERT INTO sessions (
      id, title, model, sdk_session_id, avatar, category, tags, intro, location, announcement,
      my_nickname, remark, pinned, muted, folded, saved, group_no, member_limit, join_policy,
      created_at, updated_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);
  stmt.run(
    session.id, session.title, session.model, session.sdk_session_id,
    session.avatar || "👥", session.category || "家庭",
    JSON.stringify(Array.isArray(session.tags) ? session.tags : []),
    session.intro || "", session.location || "", session.announcement || "",
    session.my_nickname || "", session.remark || "", 0, 0, 0, 1,
    session.group_no || generateGroupNo(), Number(session.member_limit) || 20,
    session.join_policy || "manual",
    session.created_at, session.updated_at
  );
  return session;
}
function updateSession(id, updates) {
  const fields = [];
  const values = [];
  if (updates.title !== void 0) {
    fields.push("title = ?");
    values.push(updates.title);
  }
  if (updates.model !== void 0) {
    fields.push("model = ?");
    values.push(updates.model);
  }
  if (updates.sdk_session_id !== void 0) {
    fields.push("sdk_session_id = ?");
    values.push(updates.sdk_session_id);
  }
  if (updates.mode !== void 0) {
    fields.push("mode = ?");
    values.push(updates.mode);
  }
  if (updates.target_agent_id !== void 0) {
    fields.push("target_agent_id = ?");
    values.push(updates.target_agent_id);
  }
  if (updates.proactive_enabled !== void 0) {
    fields.push("proactive_enabled = ?");
    values.push(updates.proactive_enabled ? 1 : 0);
  }
  if (updates.context_memory_enabled !== void 0) {
    fields.push("context_memory_enabled = ?");
    values.push(updates.context_memory_enabled ? 1 : 0);
  }
  if (fields.length === 0) return false;
  fields.push("updated_at = ?");
  values.push((/* @__PURE__ */ new Date()).toISOString());
  values.push(id);
  const stmt = db.prepare(`UPDATE sessions SET ${fields.join(", ")} WHERE id = ?`);
  const result = stmt.run(...values);
  return result.changes > 0;
}
function deleteSession(id) {
  deleteAgentSessions(id);
  db.prepare("DELETE FROM intent_logs WHERE session_id = ?").run(id);
  const stmt = db.prepare("DELETE FROM sessions WHERE id = ?");
  const result = stmt.run(id);
  return result.changes > 0;
}
function getMessagesBySession(sessionId) {
  const stmt = db.prepare("SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC");
  return stmt.all(sessionId);
}
function createMessage(message) {
  const stmt = db.prepare(`
    INSERT INTO messages (id, session_id, role, content, model, created_at, tool_calls,
      agent_id, agent_name, agent_color, intent, intent_source, intent_confidence, faq_id, reply_source, reply_to_id)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);
  stmt.run(
    message.id,
    message.session_id,
    message.role,
    message.content,
    message.model ?? null,
    message.created_at,
    message.tool_calls ?? null,
    message.agent_id ?? null,
    message.agent_name ?? null,
    message.agent_color ?? null,
    message.intent ?? null,
    message.intent_source ?? null,
    message.intent_confidence ?? null,
    message.faq_id ?? null,
    message.reply_source ?? null,
    message.reply_to_id ?? null
  );
  const updateStmt = db.prepare("UPDATE sessions SET updated_at = ? WHERE id = ?");
  updateStmt.run((/* @__PURE__ */ new Date()).toISOString(), message.session_id);
  return message;
}
function updateMessageIntent(id, updates) {
  const fields = [];
  const values = [];
  for (const [key, value] of Object.entries(updates)) {
    if (value === void 0) continue;
    fields.push(`${key} = ?`);
    values.push(value);
  }
  if (fields.length === 0) return false;
  values.push(id);
  return db.prepare(`UPDATE messages SET ${fields.join(", ")} WHERE id = ?`).run(...values).changes > 0;
}
function getAllAgents() {
  const stmt = db.prepare("SELECT * FROM agents ORDER BY sort ASC, created_at ASC");
  return stmt.all();
}
function getEnabledAgents() {
  const stmt = db.prepare("SELECT * FROM agents WHERE enabled = 1 ORDER BY sort ASC, created_at ASC");
  return stmt.all();
}
function createAgent(agent) {
  const stmt = db.prepare(`
    INSERT INTO agents (id, name, role, avatar, color, system_prompt, keywords, intents, enabled, builtin, sort, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);
  stmt.run(
    agent.id,
    agent.name,
    agent.role,
    agent.avatar,
    agent.color,
    agent.system_prompt,
    agent.keywords,
    agent.intents,
    agent.enabled,
    agent.builtin,
    agent.sort,
    agent.created_at,
    agent.updated_at
  );
  return agent;
}
function updateAgent(id, updates) {
  const fields = [];
  const values = [];
  for (const [key, value] of Object.entries(updates)) {
    if (value === void 0) continue;
    fields.push(`${key} = ?`);
    values.push(value);
  }
  if (fields.length === 0) return false;
  fields.push("updated_at = ?");
  values.push((/* @__PURE__ */ new Date()).toISOString());
  values.push(id);
  const stmt = db.prepare(`UPDATE agents SET ${fields.join(", ")} WHERE id = ?`);
  return stmt.run(...values).changes > 0;
}
function deleteAgent(id) {
  return db.prepare("DELETE FROM agents WHERE id = ?").run(id).changes > 0;
}
function getAllFaq() {
  return db.prepare("SELECT * FROM faq ORDER BY category ASC, created_at DESC").all();
}
function getEnabledFaq() {
  return db.prepare("SELECT * FROM faq WHERE enabled = 1").all();
}
function createFaq(faq) {
  const stmt = db.prepare(`
    INSERT INTO faq (id, question, answer, category, keywords, hits, enabled, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);
  stmt.run(faq.id, faq.question, faq.answer, faq.category, faq.keywords, faq.hits, faq.enabled, faq.created_at, faq.updated_at);
  return faq;
}
function updateFaq(id, updates) {
  const fields = [];
  const values = [];
  for (const [key, value] of Object.entries(updates)) {
    if (value === void 0) continue;
    fields.push(`${key} = ?`);
    values.push(value);
  }
  if (fields.length === 0) return false;
  fields.push("updated_at = ?");
  values.push((/* @__PURE__ */ new Date()).toISOString());
  values.push(id);
  return db.prepare(`UPDATE faq SET ${fields.join(", ")} WHERE id = ?`).run(...values).changes > 0;
}
function deleteFaq(id) {
  return db.prepare("DELETE FROM faq WHERE id = ?").run(id).changes > 0;
}
function increaseFaqHits(id) {
  db.prepare("UPDATE faq SET hits = hits + 1, last_hit_at = ? WHERE id = ?")
    .run(new Date().toISOString(), id);
}
function getAgentSdkSession(sessionId, agentId) {
  const row = db.prepare(
    "SELECT sdk_session_id FROM agent_sessions WHERE session_id = ? AND agent_id = ?"
  ).get(sessionId, agentId);
  return row?.sdk_session_id || null;
}
function setAgentSdkSession(sessionId, agentId, sdkSessionId) {
  db.prepare(`
    INSERT INTO agent_sessions (session_id, agent_id, sdk_session_id, updated_at)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(session_id, agent_id)
    DO UPDATE SET sdk_session_id = excluded.sdk_session_id, updated_at = excluded.updated_at
  `).run(sessionId, agentId, sdkSessionId, (/* @__PURE__ */ new Date()).toISOString());
}
function deleteAgentSessions(sessionId) {
  db.prepare("DELETE FROM agent_sessions WHERE session_id = ?").run(sessionId);
}
function createIntentLog(log) {
  const stmt = db.prepare(`
    INSERT INTO intent_logs (id, session_id, message_id, query, intent, intent_name, source, confidence, agents, faq_id, reason, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);
  stmt.run(
    log.id,
    log.session_id,
    log.message_id,
    log.query,
    log.intent,
    log.intent_name,
    log.source,
    log.confidence,
    log.agents,
    log.faq_id,
    log.reason,
    log.created_at
  );
  return log;
}
function getIntentLogs(limit = 200) {
  return db.prepare("SELECT * FROM intent_logs ORDER BY created_at DESC LIMIT ?").all(limit);
}
function countIntentsBySource() {
  return db.prepare("SELECT source, COUNT(*) as count FROM intent_logs GROUP BY source").all();
}
function countIntentsByType() {
  return db.prepare(
    "SELECT intent, MAX(intent_name) as intent_name, COUNT(*) as count FROM intent_logs GROUP BY intent ORDER BY count DESC"
  ).all();
}
function getAdminStats() {
  const one = (sql) => db.prepare(sql).get()?.c || 0;
  return {
    sessions: one("SELECT COUNT(*) as c FROM sessions"),
    messages: one("SELECT COUNT(*) as c FROM messages"),
    userMessages: one("SELECT COUNT(*) as c FROM messages WHERE role = 'user'"),
    agentMessages: one("SELECT COUNT(*) as c FROM messages WHERE role = 'assistant'"),
    faq: one("SELECT COUNT(*) as c FROM faq"),
    faqHits: one("SELECT COALESCE(SUM(hits), 0) as c FROM faq"),
    agents: one("SELECT COUNT(*) as c FROM agents"),
    intents: one("SELECT COUNT(*) as c FROM intent_logs")
  };
}
function getAdminSessions() {
  return db.prepare(`
    SELECT s.id, s.title, s.model, s.created_at, s.updated_at,
      (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count,
      (SELECT i.intent FROM intent_logs i WHERE i.session_id = s.id ORDER BY i.created_at DESC LIMIT 1) AS last_intent,
      (SELECT i.source FROM intent_logs i WHERE i.session_id = s.id ORDER BY i.created_at DESC LIMIT 1) AS last_intent_source
    FROM sessions s
    ORDER BY s.updated_at DESC
  `).all();
}
function searchMessages(options) {
  const conditions = [];
  const values = [];
  if (options.sessionId) {
    conditions.push("m.session_id = ?");
    values.push(options.sessionId);
  }
  if (options.role) {
    conditions.push("m.role = ?");
    values.push(options.role);
  }
  if (options.intent) {
    conditions.push("m.intent = ?");
    values.push(options.intent);
  }
  if (options.source) {
    conditions.push("m.intent_source = ?");
    values.push(options.source);
  }
  if (options.keyword) {
    conditions.push("(m.content LIKE ? OR s.title LIKE ?)");
    values.push(`%${options.keyword}%`, `%${options.keyword}%`);
  }
  const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";
  const limit = options.limit || 100;
  const offset = options.offset || 0;
  return db.prepare(`
    SELECT m.*, s.title AS session_title
    FROM messages m LEFT JOIN sessions s ON s.id = m.session_id
    ${where}
    ORDER BY m.created_at DESC
    LIMIT ? OFFSET ?
  `).all(...values, limit, offset);
}

// server/agents.ts
import { v4 as uuidv4 } from "uuid";
var SEED_AGENTS = [
  {
    id: "host",
    name: "\u7FA4\u4E3B\u6301 \xB7 \u5C0F\u7EC7",
    role: "\u63A7\u573A\u4E0E\u603B\u7ED3\uFF1A\u6F84\u6E05\u95EE\u9898\u3001\u534F\u8C03\u5206\u5DE5\u3001\u6536\u5C3E\u5F52\u7EB3\u7ED3\u8BBA",
    avatar: "Sparkles",
    color: "#0052d9",
    systemPrompt: "\u4F60\u662F\u667A\u80FD Agent \u7FA4\u7684\u7FA4\u4E3B\u6301\u300C\u5C0F\u7EC7\u300D\u3002\u4F60\u7684\u804C\u8D23\u662F\uFF1A1) \u7528\u4E00\u4E24\u53E5\u8BDD\u590D\u8FF0\u7528\u6237\u8BC9\u6C42\u5E76\u8BF4\u660E\u672C\u6B21\u7531\u54EA\u4E9B\u6210\u5458\u534F\u4F5C\uFF1B2) \u5728\u6210\u5458\u53D1\u8A00\u7ED3\u675F\u540E\u7ED9\u51FA\u7B80\u6D01\u7684\u603B\u7ED3\u4E0E\u4E0B\u4E00\u6B65\u5EFA\u8BAE\u3002\u4E0D\u8981\u957F\u7BC7\u5927\u8BBA\uFF0C\u4E0D\u8981\u91CD\u590D\u6210\u5458\u5DF2\u7ECF\u8BF4\u8FC7\u7684\u6280\u672F\u7EC6\u8282\uFF0C\u59CB\u7EC8\u7528\u4E2D\u6587\u56DE\u7B54\uFF0C\u8BED\u6C14\u4E13\u4E1A\u4E14\u4EB2\u548C\u3002",
    keywords: ["\u603B\u7ED3", "\u5F52\u7EB3", "\u7ED3\u8BBA", "\u68B3\u7406", "\u5E2E\u6211\u770B\u770B", "\u600E\u4E48\u529E"],
    intents: ["coordination", "chitchat"],
    enabled: true,
    builtin: true,
    sort: 0
  },
  {
    id: "kb",
    name: "\u77E5\u8BC6\u5E93\u4E13\u5458 \xB7 \u5C0F\u5E93",
    role: "FAQ \u77E5\u8BC6\u5E93\u68C0\u7D22\uFF1A\u4F18\u5148\u5F15\u7528\u77E5\u8BC6\u5E93\u539F\u6587\u7ED9\u51FA\u51C6\u786E\u7B54\u590D",
    avatar: "BookOpen",
    color: "#2ba471",
    systemPrompt: '\u4F60\u662F\u77E5\u8BC6\u5E93\u4E13\u5458\u300C\u5C0F\u5E93\u300D\u3002\u4F60\u4F18\u5148\u4F7F\u7528\u63D0\u4F9B\u7684\u3010\u77E5\u8BC6\u5E93\u547D\u4E2D\u5185\u5BB9\u3011\u56DE\u7B54\u7528\u6237\u95EE\u9898\uFF0C\u7B54\u6848\u5FC5\u987B\u5FE0\u5B9E\u4E8E\u77E5\u8BC6\u5E93\u539F\u6587\uFF0C\u4E0D\u8981\u7F16\u9020\u77E5\u8BC6\u5E93\u4E2D\u6CA1\u6709\u7684\u4FE1\u606F\u3002\u56DE\u7B54\u7ED3\u6784\uFF1A\u7ED3\u8BBA + \u5173\u952E\u6B65\u9AA4\uFF08\u7F16\u53F7\uFF09+ \u8865\u5145\u8BF4\u660E\u3002\u82E5\u77E5\u8BC6\u5E93\u5185\u5BB9\u4E0D\u8DB3\u4EE5\u56DE\u7B54\uFF0C\u660E\u786E\u8BF4\u660E"\u77E5\u8BC6\u5E93\u6682\u65E0\u6B64\u6761\u76EE"\uFF0C\u5E76\u7ED9\u51FA\u4F60\u7684\u4E13\u4E1A\u5224\u65AD\u3002\u4F7F\u7528\u4E2D\u6587\uFF0C\u63A7\u5236\u5728 200 \u5B57\u4EE5\u5185\u3002',
    keywords: ["\u600E\u4E48", "\u5982\u4F55", "\u662F\u4EC0\u4E48", "FAQ", "\u77E5\u8BC6\u5E93", "\u89C4\u5219", "\u653F\u7B56", "\u4EF7\u683C", "\u8BA1\u8D39", "\u5957\u9910", "\u9000\u6B3E", "\u53D1\u7968", "\u6743\u9650", "\u8D26\u53F7", "\u6CE8\u518C", "\u767B\u5F55"],
    intents: ["faq", "billing", "account"],
    enabled: true,
    builtin: true,
    sort: 1
  },
  {
    id: "tech",
    name: "\u6280\u672F\u652F\u6301 \xB7 \u963F\u7801\u5DE5\u7A0B\u5E08",
    role: "\u6280\u672F\u95EE\u9898\u6392\u67E5\uFF1A\u62A5\u9519\u5206\u6790\u3001API/SDK \u96C6\u6210\u3001\u90E8\u7F72\u4E0E\u6027\u80FD",
    avatar: "Wrench",
    color: "#ed7b2f",
    systemPrompt: "\u4F60\u662F\u6280\u672F\u652F\u6301\u5DE5\u7A0B\u5E08\u300C\u963F\u7801\u300D\u3002\u4F60\u64C5\u957F\u5B9A\u4F4D\u62A5\u9519\u539F\u56E0\u3001\u7ED9\u51FA\u53EF\u6267\u884C\u7684\u6392\u67E5\u6B65\u9AA4\u4E0E\u4EE3\u7801/\u914D\u7F6E\u793A\u4F8B\u3002\u56DE\u7B54\u7ED3\u6784\uFF1A\u53EF\u80FD\u539F\u56E0\uFF082-3 \u6761\uFF09+ \u6392\u67E5\u6B65\u9AA4\uFF08\u7F16\u53F7\uFF09+ \u5173\u952E\u4EE3\u7801\u6216\u547D\u4EE4\u7247\u6BB5\u3002\u4E0D\u786E\u5B9A\u7684\u5730\u65B9\u8981\u660E\u786E\u6807\u6CE8\u5047\u8BBE\uFF0C\u4E0D\u8981\u81C6\u65AD\u3002\u4F7F\u7528\u4E2D\u6587\uFF0C\u63A7\u5236\u5728 250 \u5B57\u4EE5\u5185\u3002",
    keywords: ["\u62A5\u9519", "\u9519\u8BEF", "\u5F02\u5E38", "bug", "\u5931\u8D25", "\u8D85\u65F6", "api", "sdk", "\u63A5\u53E3", "\u96C6\u6210", "\u90E8\u7F72", "\u6027\u80FD", "\u5361\u987F", "\u65E5\u5FD7", "\u6392\u67E5", "token", "\u9274\u6743", "\u5D29\u6E83"],
    intents: ["tech_support"],
    enabled: true,
    builtin: true,
    sort: 2
  },
  {
    id: "product",
    name: "\u4EA7\u54C1\u987E\u95EE \xB7 \u5C0F\u89C4",
    role: "\u4EA7\u54C1\u65B9\u6848\uFF1A\u529F\u80FD\u4ECB\u7ECD\u3001\u9009\u578B\u5BF9\u6BD4\u3001\u843D\u5730\u65B9\u6848\u4E0E\u6700\u4F73\u5B9E\u8DF5",
    avatar: "Compass",
    color: "#834ec2",
    systemPrompt: "\u4F60\u662F\u4EA7\u54C1\u987E\u95EE\u300C\u5C0F\u89C4\u300D\u3002\u4F60\u64C5\u957F\u628A\u7528\u6237\u6A21\u7CCA\u7684\u4E1A\u52A1\u8BC9\u6C42\u8F6C\u5316\u4E3A\u6E05\u6670\u7684\u4EA7\u54C1\u65B9\u6848\u3002\u56DE\u7B54\u7ED3\u6784\uFF1A\u65B9\u6848\u6982\u8FF0 + \u63A8\u8350\u9009\u578B\uFF08\u542B\u7406\u7531\uFF09+ \u843D\u5730\u6B65\u9AA4 + \u98CE\u9669\u63D0\u9192\u3002\u4E0D\u8981\u6D89\u53CA\u5177\u4F53\u4EE3\u7801\u5B9E\u73B0\u7EC6\u8282\uFF0C\u90A3\u662F\u6280\u672F\u652F\u6301\u7684\u804C\u8D23\u3002\u4F7F\u7528\u4E2D\u6587\uFF0C\u63A7\u5236\u5728 220 \u5B57\u4EE5\u5185\u3002",
    keywords: ["\u65B9\u6848", "\u9009\u578B", "\u5BF9\u6BD4", "\u63A8\u8350", "\u529F\u80FD", "\u80FD\u529B", "\u573A\u666F", "\u6700\u4F73\u5B9E\u8DF5", "\u67B6\u6784", "\u8BBE\u8BA1", "\u89C4\u5212", "\u8BC4\u4F30"],
    intents: ["product"],
    enabled: true,
    builtin: true,
    sort: 3
  },
  {
    id: "data",
    name: "\u6570\u636E\u5206\u6790\u5E08 \xB7 \u5C0F\u6570",
    role: "\u6570\u636E\u5206\u6790\uFF1A\u6307\u6807\u53E3\u5F84\u3001\u7EDF\u8BA1\u62A5\u8868\u3001\u8D8B\u52BF\u89E3\u8BFB\u4E0E\u6570\u636E\u5BFC\u51FA",
    avatar: "BarChart",
    color: "#0594fa",
    systemPrompt: "\u4F60\u662F\u6570\u636E\u5206\u6790\u5E08\u300C\u5C0F\u6570\u300D\u3002\u4F60\u64C5\u957F\u5B9A\u4E49\u6307\u6807\u53E3\u5F84\u3001\u7ED9\u51FA\u7EDF\u8BA1\u53E3\u5F84\u4E0E\u89E3\u8BFB\u7ED3\u8BBA\u3002\u56DE\u7B54\u7ED3\u6784\uFF1A\u6307\u6807\u53E3\u5F84 + \u8BA1\u7B97\u65B9\u5F0F + \u7ED3\u8BBA\u4E0E\u5EFA\u8BAE\u3002\u6570\u5B57\u5FC5\u987B\u8BF4\u660E\u6765\u6E90\u6216\u6807\u6CE8\u4E3A\u4F30\u7B97\u3002\u4F7F\u7528\u4E2D\u6587\uFF0C\u63A7\u5236\u5728 200 \u5B57\u4EE5\u5185\u3002",
    keywords: ["\u6570\u636E", "\u7EDF\u8BA1", "\u62A5\u8868", "\u6307\u6807", "\u8D8B\u52BF", "\u5206\u6790", "\u5BFC\u51FA", "\u8F6C\u5316\u7387", "\u7559\u5B58", "\u770B\u677F", "\u76D1\u63A7"],
    intents: ["data"],
    enabled: true,
    builtin: true,
    sort: 4
  }
];
function toAgent(row) {
  return {
    id: row.id,
    name: row.name,
    role: row.role,
    avatar: row.avatar,
    color: row.color,
    systemPrompt: row.system_prompt,
    keywords: safeParse(row.keywords, []),
    intents: safeParse(row.intents, []),
    enabled: row.enabled === 1,
    builtin: row.builtin === 1,
    sort: row.sort
  };
}
function safeParse(value, fallback) {
  if (!value) return fallback;
  try {
    return JSON.parse(value);
  } catch {
    return fallback;
  }
}
function ensureSeedAgents() {
  const existing = getAllAgents();
  if (existing.length === 0) {
    const now = (/* @__PURE__ */ new Date()).toISOString();
    for (const agent of SEED_AGENTS) {
      createAgent({
        id: agent.id,
        name: agent.name,
        role: agent.role,
        avatar: agent.avatar,
        color: agent.color,
        system_prompt: agent.systemPrompt,
        keywords: JSON.stringify(agent.keywords),
        intents: JSON.stringify(agent.intents),
        enabled: agent.enabled ? 1 : 0,
        builtin: agent.builtin ? 1 : 0,
        sort: agent.sort,
        created_at: now,
        updated_at: now
      });
    }
    console.log(`[Agents] \u5DF2\u521D\u59CB\u5316 ${SEED_AGENTS.length} \u4E2A\u5185\u7F6E\u7FA4\u6210\u5458`);
  }
}
function listAgents(onlyEnabled = false) {
  const rows = onlyEnabled ? getEnabledAgents() : getAllAgents();
  return rows.map(toAgent);
}
function addAgent(input) {
  const now = (/* @__PURE__ */ new Date()).toISOString();
  const row = {
    id: input.id || `agent-${uuidv4().slice(0, 8)}`,
    name: input.name,
    role: input.role || "\u7FA4\u6210\u5458",
    avatar: input.avatar || "Bot",
    color: input.color || "#0052d9",
    system_prompt: input.systemPrompt || "\u4F60\u662F\u667A\u80FD Agent \u7FA4\u7684\u6210\u5458\uFF0C\u8BF7\u57FA\u4E8E\u4F60\u7684\u804C\u8D23\u7528\u4E2D\u6587\u7B80\u6D01\u56DE\u7B54\u7528\u6237\u95EE\u9898\u3002",
    keywords: JSON.stringify(input.keywords || []),
    intents: JSON.stringify(input.intents || []),
    enabled: input.enabled === false ? 0 : 1,
    builtin: 0,
    sort: input.sort ?? 99,
    created_at: now,
    updated_at: now
  };
  createAgent(row);
  return toAgent(row);
}
function modifyAgent(id, updates) {
  const patch = {};
  if (updates.name !== void 0) patch.name = updates.name;
  if (updates.role !== void 0) patch.role = updates.role;
  if (updates.avatar !== void 0) patch.avatar = updates.avatar;
  if (updates.color !== void 0) patch.color = updates.color;
  if (updates.systemPrompt !== void 0) patch.system_prompt = updates.systemPrompt;
  if (updates.keywords !== void 0) patch.keywords = JSON.stringify(updates.keywords);
  if (updates.intents !== void 0) patch.intents = JSON.stringify(updates.intents);
  if (updates.enabled !== void 0) patch.enabled = updates.enabled ? 1 : 0;
  if (updates.sort !== void 0) patch.sort = updates.sort;
  return updateAgent(id, patch);
}
function removeAgent(id) {
  return deleteAgent(id);
}

// server/faq.ts
import { v4 as uuidv42 } from "uuid";
var SEED_FAQ = [
  {
    question: "\u5982\u4F55\u914D\u7F6E CodeBuddy API Key\uFF1F",
    answer: "1) \u5728 https://www.codebuddy.cn \u63A7\u5236\u53F0\u521B\u5EFA API Key\uFF1B2) \u5C06 Key \u5199\u5165\u73AF\u5883\u53D8\u91CF CODEBUDDY_API_KEY\uFF1B3) \u91CD\u542F\u540E\u7AEF\u670D\u52A1\u4F7F\u914D\u7F6E\u751F\u6548\u3002\u672C\u5E94\u7528\u4E5F\u652F\u6301\u5728\u300C\u8BBE\u7F6E\u300D\u9875\u586B\u5199 API Key / Auth Token\uFF0C\u914D\u7F6E\u4FDD\u5B58\u5728\u5F53\u524D\u670D\u52A1\u8FDB\u7A0B\u4E2D\u3002",
    category: "\u8D26\u53F7\u4E0E\u914D\u7F6E",
    keywords: ["api key", "\u5BC6\u94A5", "\u914D\u7F6E", "\u73AF\u5883\u53D8\u91CF", "auth token", "\u9274\u6743"],
    enabled: true
  },
  {
    question: "\u5FD8\u8BB0\u5BC6\u7801\u6216\u65E0\u6CD5\u767B\u5F55\u600E\u4E48\u529E\uFF1F",
    answer: "\u53EF\u5728\u767B\u5F55\u9875\u70B9\u51FB\u300C\u5FD8\u8BB0\u5BC6\u7801\u300D\uFF0C\u901A\u8FC7\u6CE8\u518C\u90AE\u7BB1\u6216\u624B\u673A\u53F7\u63A5\u6536\u9A8C\u8BC1\u7801\u91CD\u7F6E\u3002\u82E5\u8D26\u53F7\u88AB\u9501\u5B9A\uFF0C\u8BF7\u8054\u7CFB\u7BA1\u7406\u5458\u5728\u540E\u53F0\u89E3\u9664\u9501\u5B9A\uFF0C\u6216\u63D0\u4EA4\u5DE5\u5355\u7531\u5BA2\u670D\u4EBA\u5DE5\u6838\u9A8C\u8EAB\u4EFD\u540E\u91CD\u7F6E\u3002",
    category: "\u8D26\u53F7\u4E0E\u914D\u7F6E",
    keywords: ["\u767B\u5F55", "\u5BC6\u7801", "\u91CD\u7F6E", "\u9501\u5B9A", "\u9A8C\u8BC1\u7801", "\u6CE8\u518C"],
    enabled: true
  },
  {
    question: "\u652F\u6301\u54EA\u4E9B\u5957\u9910\u4E0E\u8BA1\u8D39\u65B9\u5F0F\uFF1F",
    answer: "\u76EE\u524D\u63D0\u4F9B\u514D\u8D39\u7248\u3001\u4E13\u4E1A\u7248\u3001\u4F01\u4E1A\u7248\u4E09\u79CD\u5957\u9910\uFF1A\u514D\u8D39\u7248\u6BCF\u6708 200 \u4E07 Token \u989D\u5EA6\uFF1B\u4E13\u4E1A\u7248\u6309\u91CF\u8BA1\u8D39\uFF0C\u652F\u6301\u66F4\u9AD8\u5E76\u53D1\u4E0E\u66F4\u957F\u4E0A\u4E0B\u6587\uFF1B\u4F01\u4E1A\u7248\u652F\u6301\u79C1\u6709\u5316\u90E8\u7F72\u4E0E\u4E13\u5C5E\u6A21\u578B\u3002\u8D26\u5355\u6309\u6708\u7ED3\u7B97\uFF0C\u53EF\u5728\u63A7\u5236\u53F0\u300C\u8D39\u7528\u4E2D\u5FC3\u300D\u67E5\u770B\u660E\u7EC6\u5E76\u7533\u8BF7\u53D1\u7968\u3002",
    category: "\u8BA1\u8D39\u4E0E\u53D1\u7968",
    keywords: ["\u4EF7\u683C", "\u5957\u9910", "\u8BA1\u8D39", "\u591A\u5C11\u94B1", "\u53D1\u7968", "\u8D26\u5355", "\u989D\u5EA6", "\u514D\u8D39"],
    enabled: true
  },
  {
    question: "\u53EF\u4EE5\u7533\u8BF7\u9000\u6B3E\u5417\uFF1F",
    answer: "\u672A\u4F7F\u7528\u7684\u9884\u4ED8\u8D39\u5957\u9910\u53EF\u5728\u8D2D\u4E70\u540E 7 \u5929\u5185\u7533\u8BF7\u5168\u989D\u9000\u6B3E\uFF1B\u6309\u91CF\u8BA1\u8D39\u90E8\u5206\u4E0D\u652F\u6301\u9000\u6B3E\u3002\u9000\u6B3E\u7533\u8BF7\u63D0\u4EA4\u540E 3-5 \u4E2A\u5DE5\u4F5C\u65E5\u9000\u56DE\u539F\u652F\u4ED8\u6E20\u9053\u3002",
    category: "\u8BA1\u8D39\u4E0E\u53D1\u7968",
    keywords: ["\u9000\u6B3E", "\u9000\u8D27", "\u53D6\u6D88\u8BA2\u5355", "\u9000\u8D39"],
    enabled: true
  },
  {
    question: "\u77E5\u8BC6\u5E93 FAQ \u5982\u4F55\u65B0\u589E\u548C\u7EF4\u62A4\uFF1F",
    answer: "\u8FDB\u5165\u300C\u7BA1\u7406\u540E\u53F0 \u2192 FAQ \u77E5\u8BC6\u5E93\u300D\uFF0C\u70B9\u51FB\u300C\u65B0\u589E\u6761\u76EE\u300D\u586B\u5199\u95EE\u9898\u3001\u7B54\u6848\u3001\u5206\u7C7B\u4E0E\u5173\u952E\u8BCD\u5373\u53EF\u3002\u5173\u952E\u8BCD\u51B3\u5B9A\u4E86\u672C\u5730\u68C0\u7D22\u7684\u53EC\u56DE\u6548\u679C\uFF0C\u5EFA\u8BAE\u8865\u9F50\u540C\u4E49\u8BCD\u3002\u6761\u76EE\u652F\u6301\u542F\u505C\uFF0C\u505C\u7528\u540E\u4E0D\u53C2\u4E0E\u68C0\u7D22\u3002",
    category: "\u77E5\u8BC6\u5E93",
    keywords: ["\u77E5\u8BC6\u5E93", "faq", "\u65B0\u589E", "\u7EF4\u62A4", "\u6761\u76EE", "\u5173\u952E\u8BCD", "\u5BFC\u5165"],
    enabled: true
  },
  {
    question: "\u610F\u56FE\u8BC6\u522B\u662F\u600E\u4E48\u5DE5\u4F5C\u7684\uFF1F",
    answer: "\u7CFB\u7EDF\u91C7\u7528\u53CC\u901A\u9053\u8BC6\u522B\uFF1A\u5148\u8D70\u672C\u5730\u89C4\u5219\u5F15\u64CE\uFF08\u5173\u952E\u8BCD + \u6B63\u5219 + \u77E5\u8BC6\u5E93\u76F8\u4F3C\u5EA6\uFF09\u5E76\u7ED9\u51FA\u7F6E\u4FE1\u5EA6\uFF1B\u5F53\u672C\u5730\u7F6E\u4FE1\u5EA6\u4F4E\u4E8E\u9608\u503C\u65F6\u81EA\u52A8\u8C03\u7528\u6A21\u578B API \u505A\u8BED\u4E49\u8BC6\u522B\u3002\u8BC6\u522B\u6765\u6E90\u4F1A\u6807\u6CE8\u4E3A\u300C\u672C\u5730\u300D\u6216\u300CAPI\u300D\uFF0C\u53EF\u5728\u7BA1\u7406\u540E\u53F0\u7EDF\u8BA1\u5360\u6BD4\u3002",
    category: "\u610F\u56FE\u8BC6\u522B",
    keywords: ["\u610F\u56FE", "\u8BC6\u522B", "\u5206\u7C7B", "\u672C\u5730", "api", "\u7F6E\u4FE1\u5EA6", "\u8DEF\u7531"],
    enabled: true
  },
  {
    question: "\u591A\u4E2A Agent \u662F\u5982\u4F55\u534F\u4F5C\u7684\uFF1F",
    answer: "\u7528\u6237\u6D88\u606F\u8FDB\u5165\u7FA4\u540E\uFF0C\u5148\u7531\u610F\u56FE\u8DEF\u7531\u9009\u51FA\u76F8\u5173\u6210\u5458\uFF08\u9ED8\u8BA4\u6700\u591A 3 \u4F4D\uFF09\uFF0C\u6210\u5458\u6309\u804C\u8D23\u987A\u5E8F\u4F9D\u6B21\u53D1\u8A00\uFF0C\u540E\u53D1\u8A00\u8005\u80FD\u770B\u5230\u524D\u9762\u6210\u5458\u7684\u5185\u5BB9\u5E76\u505A\u8865\u5145\uFF0C\u6700\u540E\u7531\u7FA4\u4E3B\u6301\u603B\u7ED3\u3002\u6BCF\u4F4D\u6210\u5458\u62E5\u6709\u72EC\u7ACB\u7684 SDK \u4F1A\u8BDD\u4E0A\u4E0B\u6587\uFF0C\u4FDD\u8BC1\u591A\u8F6E\u5BF9\u8BDD\u8FDE\u8D2F\u3002",
    category: "\u591A Agent",
    keywords: ["\u591A agent", "\u534F\u4F5C", "\u7FA4\u804A", "\u8DEF\u7531", "\u5206\u5DE5", "\u603B\u7ED3", "\u4E0A\u4E0B\u6587"],
    enabled: true
  },
  {
    question: "\u8C03\u7528\u63A5\u53E3\u8FD4\u56DE 401 \u672A\u6388\u6743\u600E\u4E48\u6392\u67E5\uFF1F",
    answer: "\u4F9D\u6B21\u68C0\u67E5\uFF1A1) API Key \u662F\u5426\u6B63\u786E\u4E14\u672A\u8FC7\u671F\uFF1B2) \u8BF7\u6C42\u5934\u662F\u5426\u4E3A Authorization: Bearer <key>\uFF1B3) \u670D\u52A1\u7AEF\u73AF\u5883\u53D8\u91CF\u662F\u5426\u5DF2\u751F\u6548\uFF08\u4FEE\u6539\u540E\u9700\u91CD\u542F\uFF09\uFF1B4) \u8D26\u53F7\u662F\u5426\u6B20\u8D39\u6216\u989D\u5EA6\u8017\u5C3D\u3002\u53EF\u5728\u670D\u52A1\u7AEF\u65E5\u5FD7\u67E5\u770B\u9274\u6743\u5931\u8D25\u539F\u56E0\u3002",
    category: "\u6280\u672F\u96C6\u6210",
    keywords: ["401", "\u672A\u6388\u6743", "\u9274\u6743\u5931\u8D25", "unauthorized", "token", "\u8FC7\u671F"],
    enabled: true
  },
  {
    question: "\u63A5\u53E3\u54CD\u5E94\u6162\u6216\u8D85\u65F6\u5982\u4F55\u5904\u7406\uFF1F",
    answer: "\u5EFA\u8BAE\uFF1A1) \u5F00\u542F\u6D41\u5F0F\uFF08SSE\uFF09\u8FD4\u56DE\u964D\u4F4E\u9996\u5B57\u5EF6\u8FDF\uFF1B2) \u51CF\u5C0F maxTurns \u4E0E\u4E0A\u4E0B\u6587\u957F\u5EA6\uFF1B3) \u5BF9\u957F\u4EFB\u52A1\u505A\u5F02\u6B65\u5316 + \u8F6E\u8BE2\uFF1B4) \u68C0\u67E5\u7F51\u7EDC\u51FA\u53E3\u4E0E\u4EE3\u7406\u914D\u7F6E\u3002\u82E5\u6301\u7EED\u8D85\u65F6\uFF0C\u8BF7\u63D0\u4F9B request-id \u63D0\u4EA4\u5DE5\u5355\u3002",
    category: "\u6280\u672F\u96C6\u6210",
    keywords: ["\u8D85\u65F6", "\u6162", "timeout", "\u6027\u80FD", "\u5EF6\u8FDF", "\u54CD\u5E94", "\u91CD\u8BD5"],
    enabled: true
  },
  {
    question: "\u5BF9\u8BDD\u5386\u53F2\u4F1A\u4FDD\u5B58\u591A\u4E45\uFF1F",
    answer: "\u6240\u6709\u5BF9\u8BDD\u8BB0\u5F55\u6301\u4E45\u5316\u5728\u672C\u5730 SQLite \u6570\u636E\u5E93\uFF08data/chat.db\uFF09\u4E2D\uFF0C\u9ED8\u8BA4\u957F\u671F\u4FDD\u5B58\u3002\u7BA1\u7406\u5458\u53EF\u5728\u7BA1\u7406\u540E\u53F0\u6309\u4F1A\u8BDD\u67E5\u770B\u4E0E\u68C0\u7D22\u8BB0\u5F55\uFF0C\u4E5F\u53EF\u76F4\u63A5\u5220\u9664\u4F1A\u8BDD\uFF08\u4F1A\u7EA7\u8054\u6E05\u7406\u8BE5\u4F1A\u8BDD\u7684\u610F\u56FE\u65E5\u5FD7\uFF09\u3002",
    category: "\u6570\u636E\u4E0E\u7BA1\u7406",
    keywords: ["\u5386\u53F2", "\u4FDD\u5B58", "\u6301\u4E45\u5316", "\u591A\u4E45", "\u5B58\u50A8", "\u5220\u9664", "\u8BB0\u5F55"],
    enabled: true
  },
  {
    question: "\u5982\u4F55\u5BFC\u51FA\u5BF9\u8BDD\u8BB0\u5F55\u4E0E\u7EDF\u8BA1\u62A5\u8868\uFF1F",
    answer: "\u5728\u7BA1\u7406\u540E\u53F0\u300C\u5BF9\u8BDD\u8BB0\u5F55\u300D\u9875\u53EF\u6309\u4F1A\u8BDD\u3001\u5173\u952E\u8BCD\u3001\u610F\u56FE\u3001\u6765\u6E90\u7B5B\u9009\uFF0C\u5E76\u5BFC\u51FA JSON \u683C\u5F0F\u6570\u636E\uFF1B\u300C\u7EDF\u8BA1\u6982\u89C8\u300D\u63D0\u4F9B\u4F1A\u8BDD\u6570\u3001\u6D88\u606F\u6570\u3001\u610F\u56FE\u5206\u5E03\u4E0E\u672C\u5730/API \u8BC6\u522B\u5360\u6BD4\u7B49\u6307\u6807\u3002",
    category: "\u6570\u636E\u4E0E\u7BA1\u7406",
    keywords: ["\u5BFC\u51FA", "\u62A5\u8868", "\u7EDF\u8BA1", "\u4E0B\u8F7D", "excel", "json"],
    enabled: true
  },
  {
    question: "\u6570\u636E\u5B89\u5168\u4E0E\u9690\u79C1\u5982\u4F55\u4FDD\u969C\uFF1F",
    answer: "\u5BF9\u8BDD\u6570\u636E\u9ED8\u8BA4\u5B58\u50A8\u5728\u672C\u5730\u6570\u636E\u5E93\uFF0C\u4E0D\u4F1A\u4E3B\u52A8\u4E0A\u4F20\u7B2C\u4E09\u65B9\uFF1BAPI Key \u7B49\u654F\u611F\u4FE1\u606F\u5728\u63A5\u53E3\u8FD4\u56DE\u65F6\u505A\u8131\u654F\u5904\u7406\u3002\u751F\u4EA7\u73AF\u5883\u5EFA\u8BAE\u5F00\u542F\u4F20\u8F93\u52A0\u5BC6\u3001\u8BBF\u95EE\u63A7\u5236\u4E0E\u5B9A\u671F\u5907\u4EFD\u3002",
    category: "\u6570\u636E\u4E0E\u7BA1\u7406",
    keywords: ["\u5B89\u5168", "\u9690\u79C1", "\u5408\u89C4", "\u8131\u654F", "\u52A0\u5BC6", "\u5907\u4EFD"],
    enabled: true
  }
];
function toItem(row) {
  return {
    id: row.id,
    question: row.question,
    answer: row.answer,
    category: row.category,
    keywords: safeParse2(row.keywords, []),
    hits: row.hits,
    lastHitAt: row.last_hit_at || null,
    enabled: row.enabled === 1,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}
function safeParse2(value, fallback) {
  if (!value) return fallback;
  try {
    return JSON.parse(value);
  } catch {
    return fallback;
  }
}
function ensureSeedFaq() {
  const existing = getAllFaq();
  if (existing.length === 0) {
    const now = (/* @__PURE__ */ new Date()).toISOString();
    for (const item of SEED_FAQ) {
      createFaq({
        id: `faq-${uuidv42().slice(0, 8)}`,
        question: item.question,
        answer: item.answer,
        category: item.category,
        keywords: JSON.stringify(item.keywords),
        hits: 0,
        enabled: item.enabled ? 1 : 0,
        created_at: now,
        updated_at: now
      });
    }
    console.log(`[FAQ] \u5DF2\u521D\u59CB\u5316 ${SEED_FAQ.length} \u6761\u77E5\u8BC6\u5E93\u6761\u76EE`);
  }
}
function tokenize(text) {
  const lower = (text || "").toLowerCase();
  const terms = /* @__PURE__ */ new Set();
  for (const w of lower.match(/[a-z0-9]{2,}/g) || []) terms.add(w);
  for (const seg of lower.match(/[\u4e00-\u9fa5]+/g) || []) {
    if (seg.length <= 2) {
      terms.add(seg);
    } else {
      for (let i = 0; i < seg.length - 1; i++) terms.add(seg.slice(i, i + 2));
    }
  }
  return Array.from(terms);
}

// ==================== FTS5 全文索引（知识库检索加速） ====================
var faqFtsAvailable = false;
function ensureFaqFts() {
  try {
    db.exec("CREATE VIRTUAL TABLE IF NOT EXISTS faq_fts USING fts5(tokens, id UNINDEXED)");
    faqFtsAvailable = true;
    rebuildFaqFts();
    if (faqFtsAvailable) console.log("[FAQ] FTS5 全文索引已启用");
  } catch (e) {
    faqFtsAvailable = false;
    console.warn("[FAQ] FTS5 不可用（" + (e?.message || e) + "），回退朴素全文匹配");
  }
}
function rebuildFaqFts() {
  if (!faqFtsAvailable) return;
  try {
    db.exec("DELETE FROM faq_fts");
    const stmt = db.prepare("INSERT INTO faq_fts (tokens, id) VALUES (?, ?)");
    const rows = db.prepare("SELECT id, question, answer, category, keywords FROM faq").all();
    const run = db.transaction(() => {
      for (const row of rows) {
        const text = [row.question, row.answer, row.category, safeParse2(row.keywords, []).join(" ")].join(" ");
        const toks = tokenize(text);
        if (toks.length > 0) stmt.run(toks.join(" "), row.id);
      }
    });
    run();
  } catch (e) {
    console.warn("[FAQ] 重建 FTS 索引失败:", e?.message || e);
    faqFtsAvailable = false;
  }
}
function searchFaq(query3, limit = 3, recordHit = false) {
  const terms = tokenize(query3);
  if (terms.length === 0) return [];
  let rows = getEnabledFaq();
  // FTS 先行：用倒排索引捞候选，再交给打分算法重排，保证"快速 + 准确评分"兼得
  if (faqFtsAvailable && rows.length > 0) {
    try {
      const match = terms.map((t) => `"${t.replace(/"/g, "\"\"")}"`).join(" OR ");
      const ftsRows = db.prepare(`
        SELECT f.* FROM faq_fts
        JOIN faq f ON f.id = faq_fts.id
        WHERE faq_fts MATCH ? AND f.enabled = 1
        ORDER BY bm25(faq_fts)
        LIMIT ?
      `).all(match, Math.max(limit * 6, 20));
      if (ftsRows.length > 0) {
        const byId = new Map(ftsRows.map((r) => [r.id, r]));
        rows = [...ftsRows, ...rows.filter((r) => !byId.has(r.id))];
      }
    } catch (e) {
      // MATCH 语法错误等：保持 rows 全量，走朴素打分
    }
  }
  const hits = [];
  for (const row of rows) {
    const question = row.question.toLowerCase();
    const answer = row.answer.toLowerCase();
    const category = row.category.toLowerCase();
    const keywords = safeParse2(row.keywords, []).map((k) => k.toLowerCase());
    let weighted = 0;
    const maxWeighted = terms.length * 3;
    const matched = [];
    for (const t of terms) {
      let w = 0;
      if (question.includes(t)) w = 3;
      else if (keywords.some((k) => k.includes(t) || t.includes(k))) w = 3;
      else if (category.includes(t)) w = 2;
      else if (answer.includes(t)) w = 1;
      if (w > 0) {
        weighted += w;
        matched.push(t);
      }
    }
    if (matched.length === 0) continue;
    const coverage = matched.length / terms.length;
    const weightRatio = weighted / maxWeighted;
    const score = Number((coverage * 0.6 + weightRatio * 0.4).toFixed(4));
    hits.push({
      id: row.id,
      question: row.question,
      answer: row.answer,
      category: row.category,
      score,
      matchedTerms: matched
    });
  }
  hits.sort((a, b) => b.score - a.score);
  const top = hits.slice(0, limit);
  if (recordHit) {
    for (const h of top) {
      try { increaseFaqHits(h.id); } catch { /* 忽略统计失败 */ }
    }
  }
  return top;
}
function markFaqHit(id) {
  increaseFaqHits(id);
}
function listFaq() {
  return getAllFaq().map(toItem);
}
function addFaq(input) {
  const now = (/* @__PURE__ */ new Date()).toISOString();
  const row = {
    id: `faq-${uuidv42().slice(0, 8)}`,
    question: input.question,
    answer: input.answer,
    category: input.category || "\u901A\u7528",
    keywords: JSON.stringify(input.keywords || []),
    hits: 0,
    enabled: input.enabled === false ? 0 : 1,
    created_at: now,
    updated_at: now
  };
  createFaq(row);
  return toItem(row);
}
function modifyFaq(id, updates) {
  const patch = {};
  if (updates.question !== void 0) patch.question = updates.question;
  if (updates.answer !== void 0) patch.answer = updates.answer;
  if (updates.category !== void 0) patch.category = updates.category;
  if (updates.keywords !== void 0) patch.keywords = JSON.stringify(updates.keywords);
  if (updates.enabled !== void 0) patch.enabled = updates.enabled ? 1 : 0;
  return updateFaq(id, patch);
}
function removeFaq(id) {
  return deleteFaq(id);
}

// server/intent.ts
import { query } from "@tencent-ai/agent-sdk";
var INTENT_DEFS = [
  {
    id: "tech_support",
    name: "\u6280\u672F\u652F\u6301",
    description: "\u62A5\u9519\u6392\u67E5\u3001API/SDK \u96C6\u6210\u3001\u90E8\u7F72\u4E0E\u6027\u80FD\u95EE\u9898",
    keywords: ["\u62A5\u9519", "\u9519\u8BEF", "\u5F02\u5E38", "\u5931\u8D25", "\u8D85\u65F6", "bug", "\u5D29\u6E83", "\u65E5\u5FD7", "\u6392\u67E5", "\u63A5\u53E3", "api", "sdk", "\u90E8\u7F72", "\u6027\u80FD", "\u5361\u987F", "\u5EF6\u8FDF", "401", "500", "\u9274\u6743"],
    patterns: [/报错|异常|失败|error|exception|超时|timeout|崩溃/i],
    agents: ["tech"]
  },
  {
    id: "billing",
    name: "\u8BA1\u8D39\u4E0E\u53D1\u7968",
    description: "\u4EF7\u683C\u3001\u5957\u9910\u3001\u8D26\u5355\u3001\u53D1\u7968\u3001\u9000\u6B3E",
    keywords: ["\u4EF7\u683C", "\u591A\u5C11\u94B1", "\u6536\u8D39", "\u5957\u9910", "\u8BA1\u8D39", "\u8D26\u5355", "\u53D1\u7968", "\u9000\u6B3E", "\u989D\u5EA6", "\u514D\u8D39", "\u7EED\u8D39", "\u8D2D\u4E70"],
    patterns: [/多少钱|价格|计费|发票|退款|套餐/i],
    agents: ["kb"]
  },
  {
    id: "account",
    name: "\u8D26\u53F7\u4E0E\u914D\u7F6E",
    description: "\u767B\u5F55\u3001\u6CE8\u518C\u3001\u6743\u9650\u3001\u5BC6\u94A5\u3001\u73AF\u5883\u914D\u7F6E",
    keywords: ["\u767B\u5F55", "\u6CE8\u518C", "\u5BC6\u7801", "\u6743\u9650", "\u8D26\u53F7", "\u5BC6\u94A5", "api key", "token", "\u914D\u7F6E", "\u73AF\u5883\u53D8\u91CF", "\u91CD\u7F6E"],
    patterns: [/登录|密码|注册|权限|api\s*key|token/i],
    agents: ["kb", "tech"]
  },
  {
    id: "data",
    name: "\u6570\u636E\u5206\u6790",
    description: "\u6307\u6807\u53E3\u5F84\u3001\u7EDF\u8BA1\u62A5\u8868\u3001\u8D8B\u52BF\u5206\u6790\u3001\u6570\u636E\u5BFC\u51FA",
    keywords: ["\u6570\u636E", "\u7EDF\u8BA1", "\u62A5\u8868", "\u6307\u6807", "\u8D8B\u52BF", "\u5206\u6790", "\u5BFC\u51FA", "\u770B\u677F", "\u8F6C\u5316\u7387", "\u7559\u5B58", "\u76D1\u63A7"],
    patterns: [/数据|统计|报表|指标|趋势|导出/i],
    agents: ["data"]
  },
  {
    id: "product",
    name: "\u4EA7\u54C1\u65B9\u6848",
    description: "\u529F\u80FD\u4ECB\u7ECD\u3001\u9009\u578B\u5BF9\u6BD4\u3001\u65B9\u6848\u8BBE\u8BA1\u4E0E\u6700\u4F73\u5B9E\u8DF5",
    keywords: ["\u65B9\u6848", "\u9009\u578B", "\u5BF9\u6BD4", "\u63A8\u8350", "\u529F\u80FD", "\u80FD\u529B", "\u573A\u666F", "\u6700\u4F73\u5B9E\u8DF5", "\u67B6\u6784", "\u89C4\u5212", "\u4F18\u52BF", "\u533A\u522B"],
    patterns: [/方案|选型|对比|推荐|最佳实践|架构/i],
    agents: ["product"]
  },
  {
    id: "faq",
    name: "\u77E5\u8BC6\u5E93\u95EE\u7B54",
    description: "\u6807\u51C6\u95EE\u9898\u54A8\u8BE2\u3001\u89C4\u5219\u653F\u7B56\u89E3\u91CA",
    keywords: ["\u600E\u4E48", "\u5982\u4F55", "\u662F\u4EC0\u4E48", "\u80FD\u4E0D\u80FD", "\u662F\u5426\u53EF\u4EE5", "\u89C4\u5219", "\u653F\u7B56", "\u8BF4\u660E", "\u6D41\u7A0B", "\u77E5\u8BC6\u5E93", "faq"],
    patterns: [/怎么|如何|是什么|能不能|可以吗|流程/i],
    agents: ["kb"]
  },
  {
    id: "coordination",
    name: "\u534F\u4F5C\u4E0E\u603B\u7ED3",
    description: "\u9700\u8981\u591A\u4F4D\u6210\u5458\u534F\u4F5C\u3001\u603B\u7ED3\u5F52\u7EB3",
    keywords: ["\u603B\u7ED3", "\u5F52\u7EB3", "\u68B3\u7406", "\u7ED3\u8BBA", "\u4E00\u8D77", "\u534F\u4F5C", "\u8BA8\u8BBA", "\u8BC4\u4F30\u4E00\u4E0B"],
    patterns: [/总结|归纳|梳理|一起|讨论/i],
    agents: ["host"]
  },
  {
    id: "chitchat",
    name: "\u95F2\u804A\u4E0E\u5176\u4ED6",
    description: "\u5BD2\u6684\u6216\u65E0\u6CD5\u5F52\u7C7B\u7684\u5185\u5BB9",
    keywords: ["\u4F60\u597D", "\u8C22\u8C22", "\u4F60\u662F\u8C01", "\u55E8", "\u5728\u5417"],
    patterns: [/^(你好|您好|hi|hello|嗨|在吗)/i],
    agents: ["host"]
  }
];
function getIntentDef(id) {
  return INTENT_DEFS.find((d) => d.id === id);
}
function recognizeLocal(text, faqHits) {
  const lower = (text || "").toLowerCase();
  const scores = INTENT_DEFS.map((def) => {
    let score = 0;
    for (const kw of def.keywords) {
      if (lower.includes(kw.toLowerCase())) {
        score += Math.max(1, Math.min(3, kw.length / 2));
      }
    }
    if (def.patterns.some((p) => p.test(text))) score += 2;
    return { def, score };
  });
  const topFaq = faqHits[0];
  if (topFaq && topFaq.score >= 0.45) {
    const faqEntry = scores.find((s) => s.def.id === "faq");
    if (faqEntry) faqEntry.score += 2 * topFaq.score;
  }
  scores.sort((a, b) => b.score - a.score);
  const best = scores[0];
  const bestScore = best?.score || 0;
  const candidates = scores.filter((s) => s.score > 0).map((s) => ({ intent: s.def.id, name: s.def.name, score: Number(s.score.toFixed(2)) })).slice(0, 3);
  if (bestScore <= 0) {
    return {
      intent: "chitchat",
      intentName: "\u95F2\u804A\u4E0E\u5176\u4ED6",
      confidence: 0.3,
      source: "local",
      agents: ["host"],
      faqId: topFaq && topFaq.score >= 0.45 ? topFaq.id : null,
      reason: "\u672C\u5730\u89C4\u5219\u672A\u547D\u4E2D\u4EFB\u4F55\u610F\u56FE\uFF0C\u6309\u95F2\u804A\u5904\u7406",
      faqHits,
      candidates
    };
  }
  const confidence = Number(Math.min(1, bestScore / 4).toFixed(2));
  return {
    intent: best.def.id,
    intentName: best.def.name,
    confidence,
    source: "local",
    agents: best.def.agents,
    faqId: topFaq && topFaq.score >= 0.45 ? topFaq.id : null,
    reason: `\u672C\u5730\u89C4\u5219\u547D\u4E2D\u300C${best.def.name}\u300D\uFF0C\u7D2F\u8BA1\u5F97\u5206 ${bestScore.toFixed(2)}`,
    faqHits,
    candidates
  };
}
var API_TIMEOUT_MS = 3e4;
function getIntentTimeoutMs() {
  const n = Number(process.env.INTENT_TIMEOUT_MS);
  if (Number.isFinite(n) && n > 0) return n;
  return isOllamaProvider() ? getOllamaTimeoutMs() : API_TIMEOUT_MS;
}
function getMaxModelAttempts() {
  const n = Number(process.env.MAX_MODEL_ATTEMPTS);
  if (Number.isFinite(n) && n > 0) return Math.min(Math.floor(n), 10);
  return 1;
}
function clampMaxAgents(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return 3;
  return Math.min(Math.floor(n), 8);
}
function getWorkspaceRoot() {
  const root = path2.resolve(__dirname2, "..");
  const configured = String(process.env.AGENT_WORKSPACE || "").trim();
  const workspace = configured ? path2.resolve(configured) : path2.join(root, "workspace");
  try {
    if (!fs.existsSync(workspace)) fs.mkdirSync(workspace, { recursive: true });
  } catch (e) {
    console.warn("[Workspace] 创建工作区失败，回退项目根目录:", e?.message || e);
    return root;
  }
  return workspace;
}

function resolveAgentCwd(requested) {
  const root = path2.resolve(__dirname2, "..");
  const workspace = getWorkspaceRoot();
  const allowed = String(process.env.AGENT_ALLOWED_ROOT || "").trim();
  const candidates = [workspace, root];
  if (allowed) {
    for (const part of allowed.split(/[;,]/)) {
      if (part.trim()) candidates.push(path2.resolve(part.trim()));
    }
  }
  if (!requested) return workspace;
  let target;
  try {
    target = path2.resolve(String(requested));
  } catch {
    return workspace;
  }
  for (const base of candidates) {
    const rel = path2.relative(base, target);
    if (rel === "" || (!rel.startsWith("..") && !path2.isAbsolute(rel))) return target;
  }
  console.warn(`[Security] \u62D2\u7EDD\u8BBF\u95EE\u8BBE\u7F6E\u76EE\u5F55\u5916\u7684 cwd: ${target}\uFF0C\u5DF2\u56DE\u9000\u5230 ${workspace}`);
  return workspace;
}
function getRawProviderKey() {
  return String(process.env.AI_PROVIDER || process.env.MODEL_PROVIDER || "").trim().toLowerCase();
}
function getAiProvider() {
  const explicit = getRawProviderKey();
  if (["ollama", "local", "本地"].includes(explicit)) return "ollama";
  if (["codebuddy", "api", "cloud", "remote"].includes(explicit)) return "codebuddy";
  // 显式配置了其它提供商 → 走 40+ 预设直连（OpenAI 兼容 / Claude 协议）
  if (explicit) return "direct";
  // 未显式配置时沿用旧探测逻辑
  if (process.env.OLLAMA_BASE_URL || process.env.OLLAMA_MODEL || process.env.OLLAMA_DEFAULT_MODEL) return "ollama";
  const codebuddyBase = String(process.env.CODEBUDDY_BASE_URL || "");
  if (/localhost:11434|127\.0\.0\.1:11434|0\.0\.0\.0:11434/i.test(codebuddyBase)) return "ollama";
  return "codebuddy";
}
function isOllamaProvider() {
  return getAiProvider() === "ollama";
}
function isDirectProvider() {
  return getAiProvider() === "direct";
}
// 解析当前直连提供商生效配置：Base URL / API Key / 模型 / 协议
function getActiveProviderConfig() {
  const key = getRawProviderKey() || "openai";
  const resolved = resolveProvider(key);
  const protocol = ["anthropic", "claude"].includes(key) ? "claude" : "openai";
  return { key, protocol, baseUrl: resolved.baseUrl, apiKey: resolved.apiKey, model: resolved.model, preset: resolved.preset };
}
function normalizeOllamaBaseUrl(value) {
  let base = String(value || process.env.OLLAMA_BASE_URL || process.env.CODEBUDDY_BASE_URL || "http://localhost:11434").trim();
  if (!/^https?:\/\//i.test(base)) base = `http://${base}`;
  return base.replace(/\/+$/, "");
}
function getOllamaDefaultModel() {
  return process.env.OLLAMA_MODEL || process.env.OLLAMA_DEFAULT_MODEL || "qwen2.5:7b";
}
function getDefaultModel() {
  const preferred = getModelRoute("default");
  if (preferred) return preferred;
  if (isOllamaProvider()) return getOllamaDefaultModel();
  if (isDirectProvider()) {
    const cfg = getActiveProviderConfig();
    if (cfg.model) return cfg.model;
    return cfg.key;
  }
  return defaultModel;
}
function getOllamaTimeoutMs() {
  const n = Number(process.env.OLLAMA_TIMEOUT_MS);
  return Number.isFinite(n) && n > 0 ? n : 12e4;
}
function getOllamaStreamIdleMs() {
  const n = Number(process.env.OLLAMA_STREAM_IDLE_MS || process.env.OLLAMA_TIMEOUT_MS);
  return Number.isFinite(n) && n > 0 ? n : 12e4;
}
function getOllamaModelsTimeoutMs() {
  const n = Number(process.env.OLLAMA_MODELS_TIMEOUT_MS);
  return Number.isFinite(n) && n > 0 ? n : 2e4;
}
function getOllamaOptions() {
  const options = {};
  const temperature = Number(process.env.OLLAMA_TEMPERATURE);
  const numCtx = Number(process.env.OLLAMA_NUM_CTX);
  if (Number.isFinite(temperature)) options.temperature = temperature;
  if (Number.isFinite(numCtx) && numCtx > 0) options.num_ctx = numCtx;
  return Object.keys(options).length > 0 ? options : void 0;
}
async function fetchOllama(pathname, body, timeoutMs = getOllamaTimeoutMs()) {
  const { response, dispose } = await openOllama(pathname, body, { timeoutMs });
  try {
    return response;
  } finally {
    dispose();
  }
}
async function openOllama(pathname, body, { timeoutMs = getOllamaTimeoutMs(), stream = false } = {}) {
  const controller = new AbortController();
  let timer = null;
  const arm = () => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => controller.abort(), timeoutMs);
  };
  const dispose = () => {
    if (timer) clearTimeout(timer);
    timer = null;
  };
  arm();
  let response;
  try {
    response = await fetch(`${normalizeOllamaBaseUrl()}${pathname}`, {
      method: body ? "POST" : "GET",
      headers: body ? { "Content-Type": "application/json" } : void 0,
      body: body ? JSON.stringify(body) : void 0,
      signal: controller.signal
    });
  } catch (error) {
    dispose();
    throw error;
  }
  if (!response.ok) {
    const text2 = await response.text().catch(() => "");
    dispose();
    throw new Error(`Ollama ${response.status}: ${text2 || response.statusText}`);
  }
  if (!stream) dispose();
  return { response, arm, dispose, controller };
}
async function getOllamaModels() {
  const response = await fetchOllama("/api/tags", null, getOllamaModelsTimeoutMs());
  const data = await response.json();
  return Array.isArray(data.models) ? data.models.map((m) => ({ modelId: m.name, name: m.name, size: m.size, modifiedAt: m.modified_at })) : [];
}
async function ollamaChatOnce({ model, systemPrompt, prompt, timeoutMs }) {
  const messages = [];
  if (systemPrompt) messages.push({ role: "system", content: systemPrompt });
  messages.push({ role: "user", content: prompt });
  const response = await fetchOllama("/api/chat", {
    model: model || getOllamaDefaultModel(),
    stream: false,
    messages,
    options: getOllamaOptions()
  }, timeoutMs);
  const data = await response.json();
  if (data.error) throw new Error(data.error);
  return data.message?.content || data.response || "";
}
function parseOllamaStreamLine(trimmed) {
  let data;
  try {
    data = JSON.parse(trimmed);
  } catch {
    return { skip: true };
  }
  if (data.error) throw new Error(data.error);
  const text2 = data.message?.content || data.response || "";
  return { text: text2, done: !!data.done };
}
async function* ollamaChatStream({ model, systemPrompt, prompt }) {
  const messages = [];
  if (systemPrompt) messages.push({ role: "system", content: systemPrompt });
  messages.push({ role: "user", content: prompt });
  const { response, arm, dispose } = await openOllama(
    "/api/chat",
    {
      model: model || getOllamaDefaultModel(),
      stream: true,
      messages,
      options: getOllamaOptions()
    },
    { timeoutMs: getOllamaStreamIdleMs(), stream: true }
  );
  try {
    const decoder = new TextDecoder();
    let buffer = "";
    for await (const chunk of response.body) {
      arm();
      buffer += decoder.decode(chunk, { stream: true });
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() || "";
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        const parsed = parseOllamaStreamLine(trimmed);
        if (parsed.skip) continue;
        if (parsed.text) yield parsed.text;
        if (parsed.done) return;
      }
    }
    const tail = buffer.trim();
    if (tail) {
      const parsed = parseOllamaStreamLine(tail);
      if (!parsed.skip && parsed.text) yield parsed.text;
    }
  } finally {
    dispose();
  }
}
async function recognizeByOllama(text, faqHits, model, availableAgents = []) {
  const intentList = INTENT_DEFS.map((d) => `- ${d.id}\uFF08${d.name}\uFF09\uFF1A${d.description}`).join("\n");
  const agentList = availableAgents.map((a) => `- ${a.id}\uFF08${a.name}\uFF09\uFF1A${a.role}`).join("\n") || "- host\uFF08\u7FA4\u4E3B\u6301\uFF09";
  const faqBrief = faqHits.map((h, i) => `${i + 1}. [${h.id}] ${h.question}\uFF08\u76F8\u4F3C\u5EA6 ${h.score}\uFF09`).join("\n") || "\uFF08\u65E0\uFF09";
  const prompt = `\u4F60\u662F\u610F\u56FE\u8BC6\u522B\u5F15\u64CE\u3002\u8BF7\u5206\u6790\u7528\u6237\u6D88\u606F\uFF0C\u53EA\u8F93\u51FA\u4E00\u4E2A JSON \u5BF9\u8C61\uFF0C\u4E0D\u8981\u8F93\u51FA\u4EFB\u4F55\u89E3\u91CA\u6587\u5B57\u3002

\u7528\u6237\u6D88\u606F\uFF1A${text}

\u53EF\u9009\u610F\u56FE\uFF1A
${intentList}

\u77E5\u8BC6\u5E93\u5019\u9009\uFF1A
${faqBrief}

\u53EF\u7528\u7FA4\u6210\u5458\uFF1A
${agentList}

\u8F93\u51FA\u683C\u5F0F\uFF1A
{"intent":"\u610F\u56FEid","confidence":0\u52301\u4E4B\u95F4\u7684\u6570\u5B57,"agents":["\u6210\u5458id"],"faq_id":"\u547D\u4E2D\u7684\u77E5\u8BC6\u5E93id\u6216\u7A7A\u5B57\u7B26\u4E32","reason":"30\u5B57\u4EE5\u5185\u7684\u5224\u65AD\u4F9D\u636E"}`;
  try {
    const result = await ollamaChatOnce({
      model,
      prompt,
      timeoutMs: getIntentTimeoutMs(),
      systemPrompt: "\u4F60\u662F\u4E25\u683C\u7684\u610F\u56FE\u8BC6\u522B\u5F15\u64CE\uFF0C\u53EA\u8F93\u51FA JSON\uFF0C\u4E0D\u8F93\u51FA\u5176\u4ED6\u5185\u5BB9\u3002"
    });
    const jsonText = extractJson(result);
    if (!jsonText) return null;
    const parsed = JSON.parse(jsonText);
    const def = getIntentDef(String(parsed.intent || "")) || INTENT_DEFS.find((d) => d.id === "chitchat");
    const rawAgents = Array.isArray(parsed.agents) ? parsed.agents : def.agents;
    const agents = rawAgents.filter((a) => availableAgents.some((x) => x.id === a) || a === "host");
    return {
      intent: def.id,
      intentName: def.name,
      confidence: Number(Math.min(1, Math.max(0, Number(parsed.confidence) || 0.6)).toFixed(2)),
      source: "ollama",
      agents: agents.length > 0 ? agents : def.agents,
      faqId: parsed.faq_id ? String(parsed.faq_id) : faqHits[0]?.score >= 0.45 ? faqHits[0].id : null,
      reason: String(parsed.reason || "Ollama \u8BED\u4E49\u8BC6\u522B"),
      faqHits,
      candidates: []
    };
  } catch (error) {
    console.error("[Intent] Ollama \u8BC6\u522B\u5931\u8D25:", error?.message || error);
    return null;
  }
}
async function recognizeByApi(text, faqHits, model, availableAgents = []) {
  if (isOllamaProvider()) return await recognizeByOllama(text, faqHits, model, availableAgents);
  const intentList = INTENT_DEFS.map((d) => `- ${d.id}\uFF08${d.name}\uFF09\uFF1A${d.description}`).join("\n");
  const agentList = availableAgents.map((a) => `- ${a.id}\uFF08${a.name}\uFF09\uFF1A${a.role}`).join("\n") || "- host\uFF08\u7FA4\u4E3B\u6301\uFF09";
  const faqBrief = faqHits.map((h, i) => `${i + 1}. [${h.id}] ${h.question}\uFF08\u76F8\u4F3C\u5EA6 ${h.score}\uFF09`).join("\n") || "\uFF08\u65E0\uFF09";
  const prompt = `\u4F60\u662F\u610F\u56FE\u8BC6\u522B\u5F15\u64CE\u3002\u8BF7\u5206\u6790\u7528\u6237\u6D88\u606F\uFF0C\u53EA\u8F93\u51FA\u4E00\u4E2A JSON \u5BF9\u8C61\uFF0C\u4E0D\u8981\u8F93\u51FA\u4EFB\u4F55\u89E3\u91CA\u6587\u5B57\u3002

\u7528\u6237\u6D88\u606F\uFF1A${text}

\u53EF\u9009\u610F\u56FE\uFF1A
${intentList}

\u77E5\u8BC6\u5E93\u5019\u9009\uFF1A
${faqBrief}

\u53EF\u7528\u7FA4\u6210\u5458\uFF1A
${agentList}

\u8F93\u51FA\u683C\u5F0F\uFF1A
{"intent":"\u610F\u56FEid","confidence":0\u52301\u4E4B\u95F4\u7684\u6570\u5B57,"agents":["\u6210\u5458id"],"faq_id":"\u547D\u4E2D\u7684\u77E5\u8BC6\u5E93id\u6216\u7A7A\u5B57\u7B26\u4E32","reason":"30\u5B57\u4EE5\u5185\u7684\u5224\u65AD\u4F9D\u636E"}`;
  try {
    const stream = query({
      prompt,
      options: {
        model: model || void 0,
        maxTurns: 1,
        allowedTools: [],
        permissionMode: "default",
        canUseTool: async () => ({ behavior: "deny", message: "\u610F\u56FE\u8BC6\u522B\u4E0D\u5141\u8BB8\u4F7F\u7528\u5DE5\u5177" }),
        systemPrompt: "\u4F60\u662F\u4E25\u683C\u7684\u610F\u56FE\u8BC6\u522B\u5F15\u64CE\uFF0C\u53EA\u8F93\u51FA JSON\uFF0C\u4E0D\u8F93\u51FA\u5176\u4ED6\u5185\u5BB9\u3002",
        executable: process.execPath
      }
    });
    let text2 = "";
    const timeout = new Promise((resolve) => setTimeout(() => resolve(null), getIntentTimeoutMs()));
    const collect = (async () => {
      for await (const msg of stream) {
        if (msg.type === "assistant") {
          const content = msg.message?.content;
          if (typeof content === "string") text2 += content;
          else if (Array.isArray(content)) {
            for (const block of content) {
              if (block?.type === "text") text2 += block.text || "";
            }
          }
        } else if (msg.type === "result") {
          break;
        }
      }
      return text2;
    })();
    const result = await Promise.race([collect, timeout]);
    if (!result) {
      console.log("[Intent] API \u8BC6\u522B\u8D85\u65F6");
      return null;
    }
    const jsonText = extractJson(result);
    if (!jsonText) return null;
    const parsed = JSON.parse(jsonText);
    const def = getIntentDef(String(parsed.intent || "")) || INTENT_DEFS.find((d) => d.id === "chitchat");
    const rawAgents = Array.isArray(parsed.agents) ? parsed.agents : def.agents;
    const agents = rawAgents.filter((a) => availableAgents.some((x) => x.id === a) || a === "host");
    return {
      intent: def.id,
      intentName: def.name,
      confidence: Number(Math.min(1, Math.max(0, Number(parsed.confidence) || 0.6)).toFixed(2)),
      source: "api",
      agents: agents.length > 0 ? agents : def.agents,
      faqId: parsed.faq_id ? String(parsed.faq_id) : faqHits[0]?.score >= 0.45 ? faqHits[0].id : null,
      reason: String(parsed.reason || "\u6A21\u578B\u8BED\u4E49\u8BC6\u522B"),
      faqHits,
      candidates: []
    };
  } catch (error) {
    console.error("[Intent] API \u8BC6\u522B\u5931\u8D25:", error?.message || error);
    return null;
  }
}
function extractJson(text) {
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start === -1 || end <= start) return null;
  return text.slice(start, end + 1);
}
async function recognizeIntent(text, options = {}) {
  const { mode = "auto", model, agents = [], localThreshold = 0.6 } = options;
  const faqHits = searchFaq(text, 3);
  const local = recognizeLocal(text, faqHits);
  if (mode === "local") return local;
  const needApi = mode === "api" || local.confidence < localThreshold || local.intent === "chitchat";
  if (!needApi) return local;
  const apiResult = await recognizeByApi(text, faqHits, model, agents.length > 0 ? agents : [{ id: "host", name: "\u7FA4\u4E3B\u6301", role: "\u63A7\u573A\u4E0E\u603B\u7ED3" }]);
  if (apiResult) return apiResult;
  return { ...local, reason: `${local.reason}\uFF08API \u8BC6\u522B\u4E0D\u53EF\u7528\uFF0C\u5DF2\u56DE\u9000\u672C\u5730\uFF09` };
}

// server/group.ts
import { query as query2 } from "@tencent-ai/agent-sdk";
import { v4 as uuidv43 } from "uuid";
var READ_ONLY_TOOLS = ["Read", "Grep", "Glob", "WebSearch", "WebFetch", "LS", "TodoRead"];
function sse(res) {
  return (event) => {
    res.write(`data: ${JSON.stringify(event)}

`);
  };
}
function pickByKeyword(text, already, agents = null) {
  const lower = (text || "").toLowerCase();
  const all = agents || listAgents(true);
  const extra = [];
  for (const agent of all) {
    if (already.includes(agent.id)) continue;
    if (agent.keywords.some((k) => lower.includes(k.toLowerCase()))) extra.push(agent.id);
  }
  return extra;
}

// 文本里是否 @ 了某个成员（支持全名与“·”后的简称）
function isAgentMentioned(text, agent) {
  const full = String(agent?.name || "").trim();
  const short = full.split(/[·|]/).pop().trim();
  const names = [...new Set([full, short].filter(Boolean))];
  const source = String(text || "");
  return names.some((n) => source.includes(`@${n}`));
}
function resolveSpeakers(text, intent, maxAgents, enableSummary, memberAgents = null) {
  const all = memberAgents && memberAgents.length > 0 ? memberAgents : listAgents(true);
  const byId = new Map(all.map((a) => [a.id, a]));
  const picked = [];
  for (const id of intent.agents) {
    if (byId.has(id) && !picked.includes(id)) picked.push(id);
  }
  for (const id of pickByKeyword(text, picked, all)) picked.push(id);
  if (picked.length === 0) picked.push("host");
  const limit = Math.max(1, maxAgents);
  // v2.9：发言顺序按群成员管理的自定义顺序（传入的 memberAgents 顺序）排，不再用全局 a.sort
  const orderIndex = new Map(all.map((a, i) => [a.id, i]));
  let speakers = picked.map((id) => byId.get(id)).filter((a) => !!a).sort((a, b) => (orderIndex.get(a.id) ?? 999) - (orderIndex.get(b.id) ?? 999)).slice(0, limit);
  const host = byId.get("host");
  if (speakers.length === 0) {
    const fallback = all[0] || {
      id: "host",
      name: "群主持",
      role: "控场与总结：澄清问题、协调分工、收尾归纳结论",
      avatar: "Sparkles",
      color: "#0052d9",
      systemPrompt: "你是智能 Agent 群的群主持。请用一两句话复述用户诉求，并给出简洁的结论与下一步建议。始终用中文回答，语气专业且亲和。",
      sort: 0
    };
    speakers = [fallback];
  }
  if (host && enableSummary && speakers.length >= 2) {
    speakers = [host, ...speakers.filter((s) => s.id !== "host"), host];
  }
  return speakers;
}
function buildAgentPrompt(params) {
  const { agent, userMessage, faqHits, history, isSummary, roundIndex } = params;
  const parts = [];
  parts.push(`\u3010\u7528\u6237\u95EE\u9898\u3011
${userMessage}`);
  if (faqHits.length > 0 && (agent.id === "kb" || agent.id === "host")) {
    const kb = faqHits.map((h, i) => `${i + 1}. [${h.category}] ${h.question}
   ${h.answer}`).join("\n");
    parts.push(`\u3010\u77E5\u8BC6\u5E93\u547D\u4E2D\u5185\u5BB9\uFF08\u4F18\u5148\u5F15\u7528\uFF0C\u4E0D\u8981\u7F16\u9020\uFF09\u3011
${kb}`);
  }
  if (history.length > 0) {
    const prev = history.map((h) => `\u300C${h.agentName}\u300D\uFF1A${h.content}`).join("\n\n");
    parts.push(`\u3010\u7FA4\u5185\u5DF2\u6709\u53D1\u8A00\u3011
${prev}`);
  }
  if (isSummary) {
    parts.push("\u3010\u5F53\u524D\u4EFB\u52A1\u3011\u8BF7\u4F5C\u4E3A\u7FA4\u4E3B\u6301\uFF0C\u7528 3-5 \u6761\u8981\u70B9\u603B\u7ED3\u4E0A\u9762\u5404\u4F4D\u6210\u5458\u7684\u7ED3\u8BBA\uFF0C\u5E76\u7ED9\u51FA\u4E0B\u4E00\u6B65\u884C\u52A8\u5EFA\u8BAE\u3002\u4E0D\u8981\u91CD\u590D\u7EC6\u8282\u3002");
  } else {
    parts.push(`\u3010\u5F53\u524D\u4EFB\u52A1\u3011\u8BF7\u4EE5\u300C${agent.name}\u300D\u7684\u8EAB\u4EFD\u53D1\u8A00\uFF08\u7B2C ${roundIndex} \u4F4D\u53D1\u8A00\uFF09\uFF0C\u805A\u7126\u4F60\u7684\u804C\u8D23\u7ED9\u51FA\u7ED3\u8BBA\u4E0E\u53EF\u6267\u884C\u5EFA\u8BAE\uFF0C\u4E0D\u8981\u590D\u8FF0\u4ED6\u4EBA\u5185\u5BB9\u3002`);
  }
  return parts.join("\n\n");
}
function isSdkErrorText(text) {
  const t = text.trim();
  return /^\s*\d{3}\s+model\b/i.test(t) || // "400 model [xxx] service info not found"
  /service info not found/i.test(t) || // 模型不存在
  /Currently supported models for your account/i.test(t) || /^\s*not logged in\b/i.test(t) || // 未登录（句首）
  /please run (the )?codebuddy login/i.test(t) || /^\s*(error|exception)[:：]/i.test(t);
}
function buildLocalFallback(agent, faqHits, model) {
  const serviceTip = isOllamaProvider() ? `请确认 Ollama 已启动、已拉取模型，并检查 OLLAMA_BASE_URL / OLLAMA_MODEL：${model}` : `可在「设置」检查登录或在输入框切换模型：${model}`;
  const tip = `\uFF08\u5F53\u524D\u6A21\u578B\u670D\u52A1\u4E0D\u53EF\u7528\uFF0C\u5DF2\u5207\u6362\u672C\u5730\u77E5\u8BC6\u5E93\u515C\u5E95\uFF0C${serviceTip}\uFF09`;
  if (agent.id === "kb" || agent.id === "host") {
    if (faqHits.length > 0) {
      const top = faqHits[0];
      return `\u3010\u672C\u5730\u77E5\u8BC6\u5E93\u547D\u4E2D\uFF5C\u76F8\u4F3C\u5EA6 ${top.score}\u3011
${top.question}
${top.answer}

${tip}`;
    }
    return `\u3010\u672C\u5730\u515C\u5E95\u3011\u77E5\u8BC6\u5E93\u4E2D\u6682\u65E0\u76F8\u5173\u6761\u76EE\u3002${tip}`;
  }
  return `\u3010\u672C\u5730\u515C\u5E95\u3011\u6211\u662F${agent.name}\uFF0C\u6682\u65F6\u65E0\u6CD5\u8C03\u7528\u6A21\u578B\u670D\u52A1\u53C2\u4E0E\u5206\u6790\u3002${tip}`;
}
// 群内发言频率：按群的 speech_mode 限制每轮发言人数
const SPEECH_LIMITS = { quiet: 1, standard: 2, active: 3, mention: 1 };
function getSpeakerLimit(sessionId, requested, mentionedCount = 0) {
  const mode = String(getSession(sessionId)?.speech_mode || "standard");
  const base = SPEECH_LIMITS[mode] ?? 2;
  if (mode === "mention") return mentionedCount > 0 ? mentionedCount : 1;
  return Math.min(base, Math.max(1, Number(requested) || base));
}
async function runGroupChat(res, options) {
  const send = sse(res);
  const { sessionId, message, model, intentMode, maxAgents, enableSummary, permissionMode, cwd, images = [], allowWrites = false, replyToId = null } = options;
  const now = (/* @__PURE__ */ new Date()).toISOString();
  const userMessageId = uuidv43();
  const quotedMessage = replyToId
    ? db.prepare("SELECT * FROM messages WHERE id = ? AND session_id = ?").get(String(replyToId), sessionId) || null
    : null;
  createMessage({
    id: userMessageId,
    session_id: sessionId,
    role: "user",
    content: message,
    model: null,
    created_at: now,
    reply_to_id: quotedMessage ? quotedMessage.id : null
  });
  send({ type: "init", sessionId, userMessageId });
  const memberRecords = getSessionMemberRecords(sessionId)
    .filter((m) => m.enabled && m.permissions.speak !== false);
  const memberPermissions = new Map(memberRecords.map((m) => [m.agentId, m.permissions]));
  const memberAgentList = listAgents(true).filter((a) => memberPermissions.has(a.id));
  const participatingAgents = memberAgentList.length > 0 ? memberAgentList : listAgents(true);
  const agentBrief = participatingAgents.map((a) => ({ id: a.id, name: a.name, role: a.role }));
  let intent;
  try {
    intent = await recognizeIntent(message, { mode: intentMode, model, agents: agentBrief });
  } catch (e) {
    intent = {
      intent: "chitchat",
      intentName: "\u95F2\u804A\u4E0E\u5176\u4ED6",
      confidence: 0.2,
      source: "local",
      agents: ["host"],
      faqId: null,
      reason: `\u610F\u56FE\u8BC6\u522B\u5F02\u5E38\uFF1A${e?.message || e}`,
      faqHits: [],
      candidates: []
    };
  }
  if (intent.faqId) markFaqHit(intent.faqId);
  createIntentLog({
    id: uuidv43(),
    session_id: sessionId,
    message_id: userMessageId,
    query: message,
    intent: intent.intent,
    intent_name: intent.intentName,
    source: intent.source,
    confidence: intent.confidence,
    agents: JSON.stringify(intent.agents),
    faq_id: intent.faqId,
    reason: intent.reason,
    created_at: (/* @__PURE__ */ new Date()).toISOString()
  });
  updateMessageIntent(userMessageId, {
    intent: intent.intent,
    intent_source: intent.source,
    intent_confidence: intent.confidence,
    faq_id: intent.faqId
  });
  send({
    type: "intent",
    intent: intent.intent,
    intentName: intent.intentName,
    source: intent.source,
    confidence: intent.confidence,
    reason: intent.reason,
    faqHits: intent.faqHits.map((h) => ({ id: h.id, question: h.question, score: h.score, category: h.category })),
    candidates: intent.candidates
  });
  // @ 指定成员：优先进入发言队列（v2.9 支持 @全体成员 / @所有人）
  const mentionedAgents = participatingAgents.filter((a) => isAgentMentioned(message, a));
  const mentionAll = /@全体成员|@所有人|@全体/.test(String(message || ""));
  if (mentionAll) {
    // @全体：本群所有启用且允许发言的成员全部进入发言队列，并突破发言频率上限
    intent.agents = [...new Set([...participatingAgents.map((a) => a.id), ...(intent.agents || [])])];
    send({
      type: "mentions",
      mentionAll: true,
      agents: participatingAgents.map((a) => ({ id: a.id, name: a.name, color: a.color, avatar: a.avatar }))
    });
  } else if (mentionedAgents.length > 0) {
    intent.agents = [...new Set([...mentionedAgents.map((a) => a.id), ...(intent.agents || [])])];
    send({
      type: "mentions",
      agents: mentionedAgents.map((a) => ({ id: a.id, name: a.name, color: a.color, avatar: a.avatar }))
    });
  }
  const explicitCount = mentionAll ? participatingAgents.length : mentionedAgents.length;
  const speakers = resolveSpeakers(message, intent, Math.max(getSpeakerLimit(sessionId, maxAgents, explicitCount), explicitCount), enableSummary, participatingAgents);
  send({
    type: "speakers",
    agents: speakers.map((a) => ({ id: a.id, name: a.name, role: a.role, color: a.color, avatar: a.avatar }))
  });
  const history = [];
  const faqHits = intent.faqHits.length > 0 ? intent.faqHits : searchFaq(message, 2, true);
  const priorMessages = isOllamaProvider() ? getMessagesBySession(sessionId).filter((m) => m.id !== userMessageId).slice(-10).map((m) => {
    const speaker = m.role === "assistant" && m.agent_name ? `assistant/${m.agent_name}` : m.role;
    return `${speaker}: ${m.content}`;
  }).join("\n\n") : "";
  let sdkAvailable = true;
  let modelAttempts = 0;
  const maxModelAttempts = getMaxModelAttempts();
  for (let i = 0; i < speakers.length; i++) {
    const agent = speakers[i];
    const memberPerm = memberPermissions.get(agent.id) || DEFAULT_MEMBER_PERMISSIONS;
    const memberDisabledTools = getMemberDisabledTools(memberPerm);
    const isSummary = enableSummary && agent.id === "host" && i === speakers.length - 1 && speakers.length >= 2;
    const assistantMessageId = uuidv43();
    send({
      type: "agent_start",
      messageId: assistantMessageId,
      agentId: agent.id,
      agentName: agent.name,
      agentColor: agent.color,
      agentAvatar: agent.avatar,
      agentRole: agent.role,
      isSummary,
      round: i + 1
    });
    let prompt = buildAgentPrompt({ agent, userMessage: quotedMessage ? `【引用${quotedMessage.role === "assistant" && quotedMessage.agent_name ? ` ${quotedMessage.agent_name}` : ""}】${String(quotedMessage.content || "").slice(0, 300)}\n\n${message}` : message, faqHits, history, isSummary, roundIndex: i + 1 });
    // 长期/短期记忆已统一注入 systemPrompt（buildEffectiveSystemPrompt），此处不再重复拼接到用户指令
    let content = "";
    let usedTools = [];
    let replySource = "api";
    if (sdkAvailable && modelAttempts < maxModelAttempts) {
      modelAttempts += 1;
      try {
        if (isOllamaProvider()) {
          replySource = "ollama";
          const ollamaPrompt = priorMessages ? `\u3010\u5386\u53F2\u5BF9\u8BDD\uFF08\u6700\u8FD1 10 \u6761\uFF09\u3011\n${priorMessages}\n\n${prompt}` : prompt;
          for await (const chunk of ollamaChatStream({ model, systemPrompt: agent.systemPrompt, prompt: ollamaPrompt })) {
            content += chunk;
            send({ type: "text", messageId: assistantMessageId, content: chunk });
          }
        } else if (isDirectProvider()) {
          // 40+ 提供商直连（OpenAI 兼容 / Claude 协议），真正使用用户在 .env / providers.json 里配置的 Key
          const cfg = getActiveProviderConfig();
          replySource = cfg.key;
          if (!cfg.baseUrl || !cfg.apiKey) {
            throw new Error(`提供商「${cfg.key}」未配置 Base URL 或 API Key，请在「后台管理 → 模型配置」中补全`);
          }
          const toolStarts = new Map();
          const runtimeTools = getRuntimeToolDefs();
          const toolsAllowed = memberPerm.tools !== false;
          const turnResult = await runAgentTurnLoop({
            provider: cfg.protocol === "claude" ? "claude" : "openai",
            providerConfig: { baseUrl: cfg.baseUrl, apiKey: cfg.apiKey },
            // 请求显式指定的模型优先（多模态必须指定 VL 模型）；未指定才回落提供商默认模型
            model: model || cfg.model,
            systemPrompt: buildEffectiveSystemPrompt(agent.systemPrompt, sessionId),
            userPrompt: promptStore.buildStepPrefix("global") + prompt,
            history: [],
            images,
            context: buildDirectContext({ cwd, permissionMode, allowWrites, disabledTools: memberDisabledTools }),
            maxTurns: getIntelligenceLevel(appIntelligenceLevel).maxTurns,
            temperature: getIntelligenceLevel(appIntelligenceLevel).temperature,
            extraTools: toolsAllowed ? runtimeTools.openai : [],
            extraToolsClaude: toolsAllowed ? runtimeTools.claude : [],
            disableTools: !toolsAllowed,
            onText: (chunk) => {
              content += chunk;
              send({ type: "text", messageId: assistantMessageId, content: chunk });
            },
            onToolStart: (t) => {
              toolStarts.set(t.id, Date.now());
              send({ type: "tool_start", messageId: assistantMessageId, toolName: t.name, toolId: t.id });
            },
            onToolResult: (t) => {
              const status = t.output?.needsConfirmation ? "pending_confirmation" : (t.output?.error ? "failed" : "completed");
              auditLogger.log({
                sessionId, agentId: agent.id, agentName: agent.name, mode: "group",
                toolName: t.name, toolInput: t.input, toolOutput: t.output, status,
                durationMs: Date.now() - (toolStarts.get(t.id) || Date.now())
              });
              if (t.output?.needsConfirmation) {
                // 档位 2：写操作被拦截 → 推送确认请求给前端，等用户允许后重发
                send({
                  type: "confirm_request",
                  messageId: assistantMessageId,
                  toolId: t.id,
                  toolName: t.output.toolName || t.name,
                  input: t.output.input || {},
                  message: t.output.message
                });
                usedTools.push({ id: t.id, name: t.name, status: "pending_confirmation" });
                send({ type: "tool_end", messageId: assistantMessageId, toolName: t.name, toolId: t.id, status: "pending_confirmation" });
              } else {
                usedTools.push({ id: t.id, name: t.name, status });
                send({ type: "tool_end", messageId: assistantMessageId, toolName: t.name, toolId: t.id, status });
              }
            }
          });
          for (const t of turnResult.tools || []) {
            if (!usedTools.some((u) => u.id === t.id)) {
              usedTools.push({ id: t.id, name: t.name, status: t.status });
            }
          }
          if (turnResult.usage) {
            recordTokenUsage({ sessionId, agentId: agent.id, mode: "group", model: model || cfg.model, usage: turnResult.usage });
          }
        } else {
          const sdkSessionId = getAgentSdkSession(sessionId, agent.id);
          const canUseTool = async (toolName, input) => {
            if (!memberPerm.tools) return { behavior: "deny", message: "该成员未开启工具权限" };
            if (!memberPerm.write && ["Write", "Edit", "MultiEdit", "NotebookEdit"].includes(toolName)) {
              return { behavior: "deny", message: "该成员未开启写文件权限" };
            }
            if (!memberPerm.command && ["Bash", "Terminal"].includes(toolName)) {
              return { behavior: "deny", message: "该成员未开启命令执行权限" };
            }
            if (permissionMode === "bypassPermissions") return { behavior: "allow", updatedInput: input };
            if (READ_ONLY_TOOLS.includes(toolName)) return { behavior: "allow", updatedInput: input };
            return { behavior: "deny", message: "\u7FA4\u804A\u6A21\u5F0F\u4E0B\u4E0D\u5141\u8BB8\u6267\u884C\u5199\u5165\u7C7B\u64CD\u4F5C" };
          };
          const stream = query2({
            prompt,
            options: {
              cwd: resolveAgentCwd(cwd),
              model,
              maxTurns: 3,
              systemPrompt: agent.systemPrompt,
              permissionMode: permissionMode || "default",
              canUseTool,
              executable: process.execPath,
              ...sdkSessionId ? { resume: sdkSessionId } : {}
            }
          });
          for await (const msg of stream) {
            if (msg.type === "system" && msg.subtype === "init") {
              const newId = msg.session_id;
              if (newId && newId !== sdkSessionId) {
                setAgentSdkSession(sessionId, agent.id, newId);
              }
            } else if (msg.type === "assistant") {
              const c = msg.message?.content;
              if (typeof c === "string" && c.trim()) {
                content += c;
                send({ type: "text", messageId: assistantMessageId, content: c });
              } else if (Array.isArray(c)) {
                for (const block of c) {
                  if (block?.type === "text" && block.text) {
                    content += block.text;
                    send({ type: "text", messageId: assistantMessageId, content: block.text });
                  } else if (block?.type === "tool_use") {
                    usedTools.push({ id: block.id, name: block.name, status: "completed" });
                  }
                }
              }
            } else if (msg.type === "result") {
              break;
            }
          }
        }
      } catch (error) {
        const msg = String(error?.message || error);
        const isAbort = error?.name === "AbortError" || /aborted/i.test(msg);
        console.error(
          `[Group] Agent ${agent.id} \u8C03\u7528\u5931\u8D25:`,
          isAbort ? `\u8D85\u65F6(${isOllamaProvider() ? getOllamaStreamIdleMs() : API_TIMEOUT_MS}ms \u65E0\u6570\u636E)` : msg,
          "\n",
          error?.stack || "(无堆栈)"
        );
        if (/not logged in|auth|login|401|unauthorized|API key|model/i.test(msg)) sdkAvailable = false;
        if (modelAttempts >= maxModelAttempts && speakers.length > 1) {
          console.warn(`[Group] \u6A21\u578B\u8FDE\u7EED\u5931\u8D25\uFF0C\u5269\u4F59 ${speakers.length - i - 1} \u4F4D\u6210\u5458\u76F4\u63A5\u4F7F\u7528\u672C\u5730\u77E5\u8BC6\u5E93\u515C\u5E95\uFF0C\u4E0D\u518D\u91CD\u8BD5`);
        }
        content = "";
      }
    }
    if (content && isSdkErrorText(content)) {
      console.warn(`[Group] Agent ${agent.id} \u8FD4\u56DE\u5185\u5BB9\u7591\u4F3C\u9519\u8BEF\uFF1A${content.slice(0, 120)}`);
      sdkAvailable = false;
      content = "";
    }
    if (!content) {
      content = buildLocalFallback(agent, faqHits, model);
      replySource = "local";
      send({ type: "text_replace", messageId: assistantMessageId, content });
    }
    createMessage({
      id: assistantMessageId,
      session_id: sessionId,
      role: "assistant",
      content,
      model,
      created_at: (/* @__PURE__ */ new Date()).toISOString(),
      tool_calls: usedTools.length > 0 ? JSON.stringify(usedTools) : null,
      agent_id: agent.id,
      agent_name: agent.name,
      agent_color: agent.color,
      intent: intent.intent,
      intent_source: intent.source,
      intent_confidence: intent.confidence,
      faq_id: intent.faqId,
      reply_source: replySource
    });
    history.push({ agentName: agent.name, content });
    send({ type: "agent_end", messageId: assistantMessageId, agentId: agent.id, source: replySource });
  }
  send({ type: "done" });
}

// server/index.ts
var __filename2 = fileURLToPath2(import.meta.url);
var __dirname2 = path2.dirname(__filename2);
const logDir = path2.join(__dirname2, "..", "logs");

// ==================== 工作流状态机 (Workflow FSM) ====================
const WorkflowStates = {
  IDLE: 'idle',
  RUNNING: 'running',
  PAUSED: 'paused',
  COMPLETED: 'completed',
  FAILED: 'failed'
};

class WorkflowFSM {
  constructor(sessionId) {
    this.sessionId = sessionId;
    this.state = WorkflowStates.IDLE;
    this.stages = [];
    this.currentStageIndex = 0;
    this.context = {};
    this.error = null;
  }

  addStage(stage) {
    this.stages.push(stage);
    return this;
  }

  async execute(input, onProgress) {
    this.state = WorkflowStates.RUNNING;
    this.context.input = input;

    try {
      for (let i = 0; i < this.stages.length; i++) {
        this.currentStageIndex = i;
        const stage = this.stages[i];
        
        onProgress({
          type: 'stage_start',
          stageIndex: i,
          stageName: stage.name,
          totalStages: this.stages.length
        });

        try {
          const result = await stage.execute(this.context);
          this.context[`stage_${i}_result`] = result;
          
          onProgress({
            type: 'stage_complete',
            stageIndex: i,
            stageName: stage.name,
            result
          });
        } catch (err) {
          onProgress({
            type: 'stage_error',
            stageIndex: i,
            stageName: stage.name,
            error: err.message
          });
          
          if (!stage.continueOnError) {
            throw err;
          }
        }
      }

      this.state = WorkflowStates.COMPLETED;
      onProgress({ type: 'workflow_complete', context: this.context });
      return this.context;
    } catch (err) {
      this.state = WorkflowStates.FAILED;
      this.error = err.message;
      onProgress({ type: 'workflow_failed', error: err.message });
      throw err;
    }
  }

  getState() {
    return {
      state: this.state,
      currentStage: this.currentStageIndex,
      totalStages: this.stages.length,
      error: this.error
    };
  }
}

// 全局工作流注册表
const activeWorkflows = new Map();

// ==================== SSE 心跳机制 ====================
function setupSSE(res) {
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.setHeader("X-Accel-Buffering", "no"); // 禁用 nginx 缓冲
  
  // 15秒心跳包
  const heartbeatInterval = setInterval(() => {
    if (!res.writableEnded) {
      res.write(":heartbeat\n\n");
    }
  }, 15000);
  
  // 清理函数
  const cleanup = () => {
    clearInterval(heartbeatInterval);
  };
  
  res.on('close', cleanup);
  res.on('error', cleanup);
  
  return { cleanup };
}

function sendSSE(res, event) {
  if (!res.writableEnded) {
    res.write(`data: ${JSON.stringify(event)}\n\n`);
  }
}

// ==================== 1v1 私聊路由 ====================
async function runPrivateChat(res, options) {
  const { sessionId, message, model, targetAgentId, permissionMode, cwd, images = [], allowWrites = false } = options;
  const now = new Date().toISOString();
  const userMessageId = uuidv44();
  
  // 创建用户消息
  createMessage({
    id: userMessageId,
    session_id: sessionId,
    role: "user",
    content: message,
    model: null,
    created_at: now
  });
  
  sendSSE(res, { type: "init", sessionId, userMessageId, mode: "private", targetAgentId });
  
  // 获取目标 Agent
  const agents = listAgents(true);
  const targetAgent = agents.find(a => a.id === targetAgentId) || agents[0];
  
  if (!targetAgent) {
    sendSSE(res, { type: "error", message: "目标 Agent 不存在" });
    return;
  }
  
  sendSSE(res, { 
    type: "agent_start", 
    messageId: uuidv44(),
    agentId: targetAgent.id,
    agentName: targetAgent.name,
    agentColor: targetAgent.color,
    agentAvatar: targetAgent.avatar,
    agentRole: targetAgent.role,
    isSummary: false,
    round: 1
  });
  
  // 构建提示词
  const history = getMessagesBySession(sessionId).slice(-10).map(m => {
    const speaker = m.role === "assistant" && m.agent_name ? `assistant/${m.agent_name}` : m.role;
    return `${speaker}: ${m.content}`;
  }).join("\n\n");
  
  let prompt = history ? `【历史对话】\n${history}\n\n【当前问题】\n${message}` : message;
  // 注入会话长期记忆
  const pmemLines = getLongTermMemoryLines(sessionId, 2);
  if (pmemLines.length > 0) prompt = `【长期记忆】\n${pmemLines.join("\n")}\n\n${prompt}`;
  
  // 调用模型
  let content = "";
  let usedTools = [];
  
  try {
    if (isOllamaProvider()) {
      for await (const chunk of ollamaChatStream({ 
        model, 
        systemPrompt: targetAgent.systemPrompt, 
        prompt 
      })) {
        content += chunk;
        sendSSE(res, { type: "text", messageId: userMessageId, content: chunk });
      }
    } else if (isDirectProvider()) {
      // 40+ 提供商直连
      const cfg = getActiveProviderConfig();
      if (!cfg.baseUrl || !cfg.apiKey) {
        throw new Error(`提供商「${cfg.key}」未配置 Base URL 或 API Key，请在「后台管理 → 模型配置」中补全`);
      }
      const toolStarts = new Map();
      const runtimeTools = getRuntimeToolDefs();
      const turnResult = await runAgentTurnLoop({
        provider: cfg.protocol === "claude" ? "claude" : "openai",
        providerConfig: { baseUrl: cfg.baseUrl, apiKey: cfg.apiKey },
        // 请求显式指定的模型优先；未指定才回落提供商默认模型
        model: model || cfg.model,
        systemPrompt: buildEffectiveSystemPrompt(targetAgent.systemPrompt, sessionId),
        userPrompt: promptStore.buildStepPrefix("global") + prompt,
        history: [],
        images,
        context: buildDirectContext({ cwd, permissionMode, allowWrites }),
        maxTurns: getIntelligenceLevel(appIntelligenceLevel).maxTurns,
        temperature: getIntelligenceLevel(appIntelligenceLevel).temperature,
        extraTools: runtimeTools.openai,
        extraToolsClaude: runtimeTools.claude,
        onText: (chunk) => {
          content += chunk;
          sendSSE(res, { type: "text", messageId: userMessageId, content: chunk });
        },
        onToolStart: (t) => {
          toolStarts.set(t.id, Date.now());
          sendSSE(res, { type: "tool_start", messageId: userMessageId, toolName: t.name, toolId: t.id });
        },
        onToolResult: (t) => {
          const status = t.output?.needsConfirmation ? "pending_confirmation" : (t.output?.error ? "failed" : "completed");
          auditLogger.log({
            sessionId, agentId: targetAgent.id, agentName: targetAgent.name, mode: "private",
            toolName: t.name, toolInput: t.input, toolOutput: t.output, status,
            durationMs: Date.now() - (toolStarts.get(t.id) || Date.now())
          });
          if (t.output?.needsConfirmation) {
            sendSSE(res, {
              type: "confirm_request",
              messageId: userMessageId,
              toolId: t.id,
              toolName: t.output.toolName || t.name,
              input: t.output.input || {},
              message: t.output.message
            });
            usedTools.push({ id: t.id, name: t.name, status: "pending_confirmation" });
            sendSSE(res, { type: "tool_end", messageId: userMessageId, toolName: t.name, toolId: t.id, status: "pending_confirmation" });
          } else {
            usedTools.push({ id: t.id, name: t.name, status });
            sendSSE(res, { type: "tool_end", messageId: userMessageId, toolName: t.name, toolId: t.id, status });
          }
        }
      });
      for (const t of turnResult.tools || []) {
        if (!usedTools.some((u) => u.id === t.id)) {
          usedTools.push({ id: t.id, name: t.name, status: t.status });
        }
      }
      if (turnResult.usage) {
        recordTokenUsage({ sessionId, agentId: targetAgent.id, mode: "private", model: model || cfg.model, usage: turnResult.usage });
      }
    } else {
      const sdkSessionId = getAgentSdkSession(sessionId, targetAgent.id);
      const stream = query2({
        prompt,
        options: {
          cwd: resolveAgentCwd(cwd),
          model,
          maxTurns: 3,
          systemPrompt: targetAgent.systemPrompt,
          permissionMode: permissionMode || "default",
          canUseTool: async (toolName, input) => {
            const permCheck = checkPermission(toolName, input, { confirmed: true });
            if (!permCheck.allowed) {
              return { behavior: "deny", message: permCheck.reason };
            }
            return { behavior: "allow", updatedInput: input };
          },
          executable: process.execPath,
          ...sdkSessionId ? { resume: sdkSessionId } : {}
        }
      });
      
      for await (const msg of stream) {
        if (msg.type === "system" && msg.subtype === "init") {
          const newId = msg.session_id;
          if (newId && newId !== sdkSessionId) {
            setAgentSdkSession(sessionId, targetAgent.id, newId);
          }
        } else if (msg.type === "assistant") {
          const c = msg.message?.content;
          if (typeof c === "string" && c.trim()) {
            content += c;
            sendSSE(res, { type: "text", messageId: userMessageId, content: c });
          } else if (Array.isArray(c)) {
            for (const block of c) {
              if (block?.type === "text" && block.text) {
                content += block.text;
                sendSSE(res, { type: "text", messageId: userMessageId, content: block.text });
              } else if (block?.type === "tool_use") {
                usedTools.push({ id: block.id, name: block.name, status: "completed" });
              }
            }
          }
        } else if (msg.type === "result") {
          break;
        }
      }
    }
  } catch (error) {
    console.error(`[PrivateChat] Agent ${targetAgent.id} 调用失败:`, error.message);
    content = `调用失败: ${error.message}`;
  }
  
  // 保存回复
  const assistantMessageId = uuidv44();
  createMessage({
    id: assistantMessageId,
    session_id: sessionId,
    role: "assistant",
    content,
    model,
    created_at: new Date().toISOString(),
    tool_calls: usedTools.length > 0 ? JSON.stringify(usedTools) : null,
    agent_id: targetAgent.id,
    agent_name: targetAgent.name,
    agent_color: targetAgent.color
  });
  
  sendSSE(res, { type: "agent_end", messageId: assistantMessageId, agentId: targetAgent.id });
  sendSSE(res, { type: "done" });
}

// ==================== 工作流执行器 ====================
async function runWorkflow(res, options) {
  const { sessionId, message, model, workflowType, permissionMode, cwd, images = null, allowWrites = false } = options;
  
  const workflow = new WorkflowFSM(sessionId);
  activeWorkflows.set(sessionId, workflow);
  
  sendSSE(res, { type: "workflow_init", sessionId, workflowType });

  const agents = listAgents(true);
  const plannerAgent = agents.find((a) => a.id === "arch") || agents[0];
  const executorAgent = agents.find((a) => a.id === "code") || agents.find((a) => a.id === "ops") || agents[0];
  const summaryAgent = agents.find((a) => a.id === "host") || plannerAgent;
  const context = buildDirectContext({ cwd, permissionMode, allowWrites });

  const buildPrevious = (ctx) => Object.entries(ctx)
    .filter(([k, v]) => k.startsWith("stage_") && k.endsWith("_result") && v)
    .map(([, v], i) => `阶段${i + 1}结果：${String(v.output || "").slice(0, 900)}`)
    .filter((s) => s.trim())
    .join("\n\n");

  const stageDefs = [];
  const tpl = db.prepare("SELECT * FROM workflow_templates WHERE id = ? AND enabled = 1")
    .get(String(workflowType || ""));
  if (tpl) {
    let names = [];
    try { names = JSON.parse(tpl.stages); } catch { names = []; }
    sendSSE(res, { type: "workflow_template", id: tpl.id, name: tpl.name, stages: names });
    names.filter((n) => n && String(n).trim()).forEach((n, idx) => {
      stageDefs.push({
        name: String(n).trim(),
        agent: idx === 0 ? plannerAgent : executorAgent,
        prompt: ({ message, previous }) =>
          `你是工作流「${tpl.name}」的第 ${idx + 1} 阶段：${n}。\n` +
          `用户需求：${message}\n\n` +
          (previous ? `${previous}\n\n` : "") +
          `请完成本阶段并给出真实结果；需要读文件/写文件/执行命令时必须调用工具，禁止只描述计划。`
      });
    });
  } else if (workflowType === "code_review") {
    stageDefs.push(
      {
        name: "需求分析", agent: plannerAgent,
        prompt: ({ message }) =>
          `你是「需求分析」阶段。请分析下面的代码审查需求，明确审查范围、关键风险点与验收标准。\n用户需求：${message}`
      },
      {
        name: "代码审查", agent: executorAgent,
        prompt: ({ message, previous }) =>
          `你是「代码审查」阶段。请用 read_file / search_files 工具读取真实代码，按 正确性/边界条件/性能/可维护性 输出问题，标注文件与行号。禁止编造。\n用户需求：${message}\n\n${previous}`
      },
      {
        name: "生成报告", agent: summaryAgent,
        prompt: ({ message, previous }) =>
          `你是「生成报告」阶段。请汇总真实审查结果，输出问题清单与修复建议。不要编造未读到的文件。\n用户需求：${message}\n\n${previous}`
      }
    );
  } else if (workflowType === "file_operation") {
    stageDefs.push(
      {
        name: "文件扫描", agent: executorAgent,
        prompt: ({ message }) =>
          `你是「文件扫描」阶段。请用 list_directory / search_files 工具真实盘点相关目录，列出文件与结构。\n用户需求：${message}`
      },
      {
        name: "内容分析", agent: plannerAgent,
        prompt: ({ message, previous }) =>
          `你是「内容分析」阶段。基于文件盘点结果分析需要执行的操作与风险。\n用户需求：${message}\n\n${previous}`
      },
      {
        name: "执行操作", agent: executorAgent,
        prompt: ({ message, previous }) =>
          `你是「执行操作」阶段。请用工具真实执行文件操作（写入/编辑/移动），完成后复查并报告真实路径。禁止空谈。\n用户需求：${message}\n\n${previous}`
      }
    );
  } else {
    // 默认工作流：分析 -> 执行 -> 总结
    stageDefs.push(
      {
        name: "分析阶段", agent: plannerAgent,
        prompt: ({ message }) =>
          `你是「分析阶段」。请分析用户需求，输出：目标、约束、3-5 条执行步骤、验收标准。不要调用工具。\n用户需求：${message}`
      },
      {
        name: "执行阶段", agent: executorAgent,
        prompt: ({ message, previous }) =>
          `你是「执行阶段」。请基于分析结果真实执行任务，必须调用工具产出文件或结果；完成后报告真实产出路径。\n用户需求：${message}\n\n${previous}`
      },
      {
        name: "总结阶段", agent: summaryAgent,
        prompt: ({ message, previous }) =>
          `你是「总结阶段」。请汇总真实产出：创建/修改的文件路径、关键结果、遗留问题。没有真实产出就明确说明，禁止编造。\n用户需求：${message}\n\n${previous}`
      }
    );
  }

  const totalStages = stageDefs.length;
  stageDefs.forEach((def, idx) => {
    workflow.addStage({
      name: def.name,
      continueOnError: true,
      execute: async (ctx) => {
        const previous = buildPrevious(ctx);
        const prompt = def.prompt({ message, previous });
        const messageId = uuidv44();
        sendSSE(res, {
          type: "agent_start", messageId,
          agentId: def.agent.id, agentName: def.agent.name,
          agentColor: def.agent.color, agentAvatar: def.agent.avatar,
          agentRole: def.agent.role, isSummary: false, round: idx + 1
        });
        const r = await runDirectTurn(res, {
          messageId, model, agent: def.agent, prompt,
          images: idx === 0 ? images : null,
          context, maxTurns: 4, sessionId, mode: "workflow"
        });
        sendSSE(res, { type: "agent_end", messageId, agentId: def.agent.id, source: "direct" });
        return {
          output: r.content,
          tools: (r.usedTools || []).map((t) => t.name),
          verified: (r.usedTools || []).length > 0
        };
      }
    });
  });
  
  try {
    await workflow.execute(message, (progress) => {
      if (progress.type === "workflow_complete") {
        sendSSE(res, { type: "workflow_complete", context: progress.context, totalStages });
        return;
      }
      // 保留外层 type，避免被 progress.type 覆盖导致前端收不到
      sendSSE(res, { ...progress, type: "workflow_progress", event: progress.type });
    });
  } catch (err) {
    sendSSE(res, { type: "workflow_error", error: err.message });
  } finally {
    activeWorkflows.delete(sessionId);
  }
}

// ==================== 6 种新交互模式实现 ====================

// 真实执行一轮 Agent 对话：直连模型 + 真实工具 + 确认流 + SSE 事件 + 审计日志
async function runDirectTurn(res, { messageId, model, agent, prompt, images = null, context = {}, maxTurns = null, sessionId = null, mode = null }) {
  const cfg = getActiveProviderConfig();
  if (!cfg.baseUrl || !cfg.apiKey) {
    throw new Error(`提供商「${cfg.key}」未配置 Base URL 或 API Key，请在「后台管理 → 模型配置」中补全`);
  }
  // 智力档位决定工具轮数上限
  const iq = getIntelligenceLevel(appIntelligenceLevel);
  const effectiveMaxTurns = Number.isFinite(maxTurns) && maxTurns > 0 ? maxTurns : iq.maxTurns;
  // 提示词注入（系统 + 风格人设 + 技能插件 + 长短期记忆，强制模式下硬约束）
  const systemPrompt = buildEffectiveSystemPrompt(agent?.systemPrompt || '', sessionId);
  const stepPrefix = promptStore.buildStepPrefix('global');
  const finalPrompt = stepPrefix ? `${stepPrefix}${prompt}` : prompt;

  let content = "";
  const usedTools = [];
  const toolStarts = new Map(); // toolId -> 起始时间
  const runtimeTools = getRuntimeToolDefs();
  const turnResult = await runAgentTurnLoop({
    provider: cfg.protocol === "claude" ? "claude" : "openai",
    providerConfig: { baseUrl: cfg.baseUrl, apiKey: cfg.apiKey },
    model: model || cfg.model,
    systemPrompt,
    userPrompt: finalPrompt,
    history: [],
    images: Array.isArray(images) ? images : [],
    context,
    maxTurns: effectiveMaxTurns,
    temperature: iq.temperature,
    extraTools: runtimeTools.openai,
    extraToolsClaude: runtimeTools.claude,
    onText: (chunk) => {
      content += chunk;
      sendSSE(res, { type: "text", messageId, content: chunk });
    },
    onToolStart: (t) => {
      toolStarts.set(t.id, Date.now());
      sendSSE(res, { type: "tool_start", messageId, toolName: t.name, toolId: t.id });
    },
    onToolResult: (t) => {
      const durationMs = Date.now() - (toolStarts.get(t.id) || Date.now());
      const status = t.output?.needsConfirmation ? 'pending_confirmation' : (t.output?.error ? 'failed' : 'completed');
      auditLogger.log({
        sessionId, agentId: agent?.id, agentName: agent?.name, mode,
        toolName: t.name, toolInput: t.input, toolOutput: t.output, status, durationMs
      });
      if (t.output?.needsConfirmation) {
        sendSSE(res, {
          type: "confirm_request", messageId, toolId: t.id,
          toolName: t.output.toolName || t.name, input: t.output.input || {}, message: t.output.message
        });
        usedTools.push({ id: t.id, name: t.name, status: "pending_confirmation" });
        sendSSE(res, { type: "tool_end", messageId, toolName: t.name, toolId: t.id, status: "pending_confirmation" });
      } else {
        usedTools.push({ id: t.id, name: t.name, status });
        sendSSE(res, { type: "tool_end", messageId, toolName: t.name, toolId: t.id, status });
      }
    }
  });
  for (const t of turnResult.tools || []) {
    if (!usedTools.some((u) => u.id === t.id)) usedTools.push({ id: t.id, name: t.name, status: t.status });
  }
  // 记录 token / 上下文用量
  if (turnResult.usage) {
    recordTokenUsage({
      sessionId, agentId: agent?.id, mode,
      model: model || cfg.model,
      usage: turnResult.usage
    });
  }
  // 写入短期记忆（1 小时有效），供后续轮次注入
  if (sessionId && content) {
    try {
      hierarchicalMemory.storeContextMemory(sessionId, {
        goal: String(prompt).slice(0, 200),
        result: String(content).slice(0, 600),
        tools: usedTools.map((t) => t.name)
      }, 'conversation');
    } catch (e) {
      console.warn('[Memory] 短期记忆写入失败(忽略):', e?.message || e);
    }
  }
  return { content, usedTools, usage: turnResult.usage || null };
}

// 写入 token 用量
function recordTokenUsage({ sessionId, agentId, mode, model, usage }) {
  try {
    db.prepare(`
      INSERT INTO token_usage (id, session_id, agent_id, mode, model, prompt_tokens, completion_tokens, total_tokens, cached_tokens, context_messages, context_chars, estimated, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      `tu-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      sessionId || null, agentId || null, mode || null, model || null,
      usage.prompt_tokens || 0, usage.completion_tokens || 0, usage.total_tokens || 0,
      usage.cached_tokens || 0,
      usage.context_messages || 0, usage.context_chars || 0,
      usage.estimated ? 1 : 0, new Date().toISOString()
    );
  } catch (e) {
    console.warn('[TokenUsage] 写入失败(忽略):', e?.message || e);
  }
}

// ==================== 自主性 L2/L3 辅助 ====================

// 单次模型调用（不做工具循环），用于验收判定等轻量判断
async function callModelOnce({ system = "", prompt = "" }) {
  const cfg = getActiveProviderConfig();
  if (!cfg.baseUrl || !cfg.apiKey) throw new Error(`提供商「${cfg.key}」未配置 Base URL 或 API Key`);
  const r = await runAgentTurnLoop({
    provider: cfg.protocol === "claude" ? "claude" : "openai",
    providerConfig: { baseUrl: cfg.baseUrl, apiKey: cfg.apiKey },
    model: getModelRoute("quick") || cfg.model,
    systemPrompt: system,
    userPrompt: prompt,
    history: [],
    images: [],
    context: {},
    maxTurns: 1,
    disableTools: true,
    onText: () => {},
    onToolStart: () => {},
    onToolResult: () => {}
  });
  return r.content || "";
}

// 后台队列任务执行：复用自主代理真实执行链路，事件写入空 sink
async function runQueuedAutonomousTask(task) {
  appendJournal({
    taskId: task.id,
    sessionId: task.session_id,
    eventType: "task_start",
    summary: `开始执行：${String(task.goal || "").slice(0, 200)}`,
    details: { priority: task.priority, dueAt: task.dueAt, allowWrites: task.allowWrites }
  });
  const sink = {
    writableEnded: false,
    write() {},
    end() {},
    on() {},
    setHeader() {}
  };
  await runAutonomousAgent(sink, {
    sessionId: task.session_id,
    message: task.goal,
    model: task.model || getDefaultModel(),
    permissionMode: Number(task.allow_writes) !== 0 ? "bypassPermissions" : "default",
    cwd: null,
    images: null,
    allowWrites: Number(task.allow_writes) !== 0,
    resumeTaskId: task.id
  });
}

// 队列任务结束后的收尾：回写会话消息、回算目标进度、失败提醒
function handleQueuedTaskFinished(task) {
  if (!task) return;
  const runStatus = task._runStatus || task.status;
  try {
    appendJournal({
      taskId: task.id,
      sessionId: task.session_id,
      eventType: "task_finished",
      summary: `任务${runStatus === "completed" ? "完成" : runStatus === "paused" ? "暂停" : "结束"}：${String(task.goal || "").slice(0, 200)}`,
      details: { status: runStatus, lastError: task.last_error || null, replanCount: task.replan_count || 0 },
      artifacts: task.step_results || null
    });
    if (task._autoRetry) {
      appendJournal({
        taskId: task.id,
        sessionId: task.session_id,
        eventType: "task_retry_scheduled",
        summary: `任务失败，已安排第 ${task.auto_retry} 次自动重试：${task.due_at ? new Date(task.due_at).toLocaleString() : "-"}`,
        details: { autoRetry: task.auto_retry, dueAt: task.due_at, lastError: task.last_error || null }
      });
      return;
    }
    if (task._rescheduledTo) {
      appendJournal({
        taskId: task.id,
        sessionId: task.session_id,
        eventType: "task_rescheduled",
        summary: `循环任务已安排下一次执行：${new Date(task._rescheduledTo).toLocaleString()}`,
        details: { recurrence: task.recurrence, nextRunAt: task._rescheduledTo }
      });
    }
    if (runStatus === "completed") {
      createNotification({
        type: "task",
        title: `后台任务完成：${String(task.goal || "").slice(0, 60)}`,
        body: `共 ${task.total_steps || 0} 步${task.recurrence && task.recurrence !== "none" ? "，已安排下一次循环" : ""}`,
        sessionId: task.session_id
      });
    } else if (runStatus === "paused") {
      createNotification({
        type: "task",
        title: `后台任务暂停：${String(task.goal || "").slice(0, 60)}`,
        body: task.last_error || "可在任务队列中重试",
        sessionId: task.session_id
      });
    }
    sendWebhook("task.finished", {
      taskId: task.id,
      sessionId: task.session_id,
      status: runStatus,
      goal: task.goal,
      error: task.last_error || null,
      nextRunAt: task._rescheduledTo || null
    });
    if (task.goal_id) {
      try { goalService.refreshProgress(task.goal_id); } catch { /* 忽略 */ }
    }
    const statusText = runStatus === "completed" ? "完成" : runStatus === "paused" ? "暂停" : runStatus;
    if (task.session_id) {
      let stepResults = [];
      try { stepResults = JSON.parse(task.step_results || "[]"); } catch { stepResults = []; }
      const lines = stepResults.filter(Boolean).map((r, i) =>
        `${i + 1}. ${r.name}｜评分 ${Number(r.score || 0).toFixed(1)}${r.error ? `｜失败：${r.error}` : ""}`
      );
      const content = `【后台自主任务】状态：${statusText}\n目标：${task.goal}\n${lines.length ? `执行记录：\n${lines.join("\n")}` : ""}`;
      try {
        createMessage({
          id: uuidv44(),
          session_id: task.session_id,
          role: "assistant",
          content,
          model: task.model || null,
          created_at: new Date().toISOString(),
          agent_name: "自主代理（后台）",
          agent_color: "#7b1fa2",
          reply_source: "queue"
        });
      } catch (e) {
        console.warn("[Queue] 结果回写会话失败(忽略):", e?.message || e);
      }
      if (runStatus !== "completed") {
        proactivePatrol.addSuggestion(task.session_id, {
          type: "queue_task_failed",
          priority: "high",
          message: `后台任务「${String(task.goal).slice(0, 40)}」${statusText}${task.last_error ? `：${String(task.last_error).slice(0, 60)}` : ""}，可在后台任务队列中重试。`,
          action: "resume_task",
          data: { autonomousTaskId: task.id }
        });
      }
    }
  } catch (e) {
    console.warn("[Queue] 任务收尾失败(忽略):", e?.message || e);
  }
}

// 自主任务规划（初始规划与失败重规划共用）
async function requestAutonomousPlan({ goalText, model, cwd, memoryBlock = "", strategyHints = "", replanContext = "" }) {
  const cfg = getActiveProviderConfig();
  if (!cfg.baseUrl || !cfg.apiKey) return [];
  const systemPrompt =
    `你是严格的任务规划器，只输出 JSON 数组，不要输出解释文字。\n` +
    `规划时应避免重复过往失败的步骤。` +
    (memoryBlock ? `\n\n${memoryBlock}` : "") +
    (strategyHints ? `\n\n${strategyHints}` : "");
  const userPrompt = replanContext
    ? replanContext
    : `你是自主任务规划器。请把用户任务拆解为 2-5 个可执行步骤。\n` +
      `只输出 JSON 数组（不要任何其他文本），每项格式 {"name":"步骤名","action":"该步骤要做什么"}\n\n` +
      `用户任务：${goalText}`;
  const planRes = await runAgentTurnLoop({
    provider: cfg.protocol === "claude" ? "claude" : "openai",
    providerConfig: { baseUrl: cfg.baseUrl, apiKey: cfg.apiKey },
    model: model || cfg.model,
    systemPrompt,
    userPrompt,
    history: [],
    images: [],
    context: { cwd: resolveAgentCwd(cwd), confirmed: true, knowledgeSearch: (q, l) => searchFaq(q, l) },
    maxTurns: 1,
    onText: () => {},
    onToolStart: () => {},
    onToolResult: () => {}
  });
  const raw = planRes.content || "";
  const jsonText = raw.slice(raw.indexOf("["), raw.lastIndexOf("]") + 1);
  if (!jsonText) return [];
  const parsed = JSON.parse(jsonText);
  if (!Array.isArray(parsed)) return [];
  return parsed.map((s) => {
    const rawName = s && typeof s === "object"
      ? (s.name || s.action || s.task || s.title || s.step || s.description || JSON.stringify(s))
      : String(s);
    return { name: String(rawName).slice(0, 40), action: String(rawName) };
  }).filter((s) => s.name && s.name !== "undefined");
}

// ==================== 沙箱 / MCP 运行时 ====================
function sandboxEnabled() {
  return String(process.env.SANDBOX_MODE || "0") === "1";
}

function getSandboxRoot() {
  return path.join(__dirname, "..", "data", "sandbox");
}

function getMcpToolDefs() {
  try { return mcpManager.getToolDefs(); } catch { return { openai: [], claude: [], count: 0 }; }
}

function getPluginToolDefs() {
  try { return buildPluginTools(db); } catch { return { openai: [], claude: [], index: new Map(), readOnlyNames: [], count: 0 }; }
}

// 合并内置之外的两类声明式工具
function getRuntimeToolDefs() {
  const mcp = getMcpToolDefs();
  const plugin = getPluginToolDefs();
  return {
    openai: [...plugin.openai, ...mcp.openai],
    claude: [...plugin.claude, ...mcp.claude],
    count: plugin.count + mcp.count
  };
}

function parseJsonArray(value) {
  try {
    const parsed = JSON.parse(value || "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function toNote(row) {
  if (!row) return null;
  return {
    ...row,
    tags: parseJsonArray(row.tags),
    pinned: Boolean(row.pinned)
  };
}

function createNoteRecord({
  title, content_md, tags = [], owner = "human", visibility = "shared", task_id = null, pinned = false
}) {
  const id = `note-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const now = new Date().toISOString();
  db.prepare(`
    INSERT INTO notes (id, title, content_md, tags, owner, visibility, task_id, pinned, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    id,
    String(title || "未命名笔记").slice(0, 200),
    String(content_md || ""),
    JSON.stringify(Array.isArray(tags) ? tags.slice(0, 30).map((t) => String(t)) : []),
    owner === "agent" ? "agent" : "human",
    ["private", "shared", "agent"].includes(visibility) ? visibility : "shared",
    task_id || null,
    pinned ? 1 : 0,
    now,
    now
  );
  return toNote(db.prepare("SELECT * FROM notes WHERE id = ?").get(id));
}

function getNoteRecord(id) {
  return toNote(db.prepare("SELECT * FROM notes WHERE id = ?").get(String(id || "")));
}

function searchNoteRecords(query, limit = 5, includePrivate = false) {
  const q = `%${String(query || "").trim()}%`;
  const lim = Math.min(Math.max(Number(limit) || 5, 1), 50);
  const rows = includePrivate
    ? db.prepare(`SELECT * FROM notes WHERE title LIKE ? OR content_md LIKE ? ORDER BY pinned DESC, updated_at DESC LIMIT ?`).all(q, q, lim)
    : db.prepare(`SELECT * FROM notes WHERE visibility != 'private' AND (title LIKE ? OR content_md LIKE ?) ORDER BY pinned DESC, updated_at DESC LIMIT ?`).all(q, q, lim);
  return rows.map(toNote);
}

function appendJournal({ taskId = null, sessionId = null, eventType = "note", summary = "", details = null, artifacts = null }) {
  try {
    const id = `journal-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    db.prepare(`
      INSERT INTO agent_journal (id, task_id, session_id, event_type, summary, details, artifacts, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      id,
      taskId || null,
      sessionId || null,
      String(eventType || "note"),
      String(summary || "").slice(0, 1000),
      details == null ? null : (typeof details === "string" ? details : JSON.stringify(details)),
      artifacts == null ? null : (typeof artifacts === "string" ? artifacts : JSON.stringify(artifacts)),
      new Date().toISOString()
    );
    return id;
  } catch (e) {
    console.warn("[Journal] 写入失败(忽略):", e?.message || e);
    return null;
  }
}

const notesToolApi = {
  write({ title, content, tags = [], visibility = "shared", task_id = null }) {
    try {
      const note = createNoteRecord({
        title, content_md: content, tags,
        owner: "agent",
        visibility: visibility === "agent" ? "agent" : "shared",
        task_id
      });
      appendJournal({
        taskId: task_id, sessionId: null, eventType: "note",
        summary: `写入笔记：${note.title}`,
        artifacts: { noteId: note.id }
      });
      return { success: true, id: note.id, message: `笔记已写入：${note.title}` };
    } catch (e) {
      return { error: true, message: `笔记写入失败：${e?.message || e}` };
    }
  },
  read({ id }) {
    const note = getNoteRecord(id);
    if (!note) return { error: true, message: "笔记不存在" };
    if (note.visibility === "private") return { error: true, message: "该笔记为私人笔记，AI 不可读取" };
    return {
      success: true,
      note: {
        id: note.id, title: note.title, content: note.content_md,
        tags: note.tags, owner: note.owner, visibility: note.visibility,
        task_id: note.task_id, updated_at: note.updated_at
      }
    };
  },
  search({ query, limit = 5 }) {
    const rows = searchNoteRecords(query, limit, false);
    return {
      success: true,
      count: rows.length,
      results: rows.map((n) => ({
        id: n.id, title: n.title, tags: n.tags, owner: n.owner,
        visibility: n.visibility, task_id: n.task_id, updated_at: n.updated_at,
        snippet: String(n.content_md || "").replace(/\s+/g, " ").slice(0, 300)
      }))
    };
  }
};

// 构建直连执行上下文（权限 + 知识库检索/写入 + 长期记忆 + 沙箱 + MCP）
function buildDirectContext({ cwd, permissionMode, allowWrites = false, memoryLines = "", disabledTools = [] }) {
  const baseCwd = resolveAgentCwd(cwd);
  const workspaceRoot = path2.resolve(__dirname2, ".."); // 路径护栏：允许项目根与工作区
  const sandboxRoot = sandboxEnabled() ? getSandboxRoot() : null;
  const pluginTools = getPluginToolDefs();
  return {
    cwd: baseCwd,
    workspaceRoot,
    sandboxRoot,
    permissionMode,
    confirmed: permissionMode === "bypassPermissions" || allowWrites === true,
    disabledTools: Array.isArray(disabledTools) ? disabledTools : [],
    readOnlyTools: pluginTools.readOnlyNames,
    knowledgeSearch: (q, l) => searchFaq(q, l, true),
    notes: notesToolApi,
    addKnowledge: (item) => {
      try {
        const faqItem = addFaq({
          question: item.question,
          answer: item.answer,
          category: item.category || "通用",
          keywords: item.keywords || []
        });
        rebuildFaqFts();
        return { success: true, id: faqItem.id, message: `知识库条目已添加：${item.question.slice(0, 40)}` };
      } catch (e) {
        return { error: true, message: `知识库写入失败：${e?.message || e}` };
      }
    },
    longTermMemory: memoryLines,
    mcpCall: (name, args) => mcpManager.call(name, args),
    pluginCall: (name, args) => {
      const entry = pluginTools.index.get(name);
      if (!entry) return { error: true, message: `插件工具不存在: ${name}` };
      return callPluginTool(entry, args, { workspaceRoot, sandboxRoot });
    }
  };
}

// 读取该会话的长期记忆（供注入提示词）
function getLongTermMemoryLines(sessionId, limit = 3) {
  try {
    const rows = db.prepare(
      "SELECT content FROM context_memory WHERE session_id = ? AND memory_type = 'long_term' ORDER BY created_at DESC LIMIT ?"
    ).all(sessionId, limit);
    const texts = rows.map((r) => {
      try { const o = JSON.parse(r.content); return o.summary || o.text || ""; } catch { return ""; }
    }).filter(Boolean);
    return texts;
  } catch {
    return [];
  }
}

// 读取该会话的短期记忆（1 小时内有效）
function getShortTermMemoryLines(sessionId, limit = 3) {
  try {
    const rows = db.prepare(
      "SELECT content FROM context_memory WHERE session_id = ? AND memory_type = 'conversation' AND (expires_at IS NULL OR expires_at > ?) ORDER BY created_at DESC LIMIT ?"
    ).all(sessionId, new Date().toISOString(), limit);
    return rows.map((r) => {
      try {
        const o = JSON.parse(r.content);
        const goal = String(o.goal || "").slice(0, 80);
        const result = String(o.result || "").slice(0, 160);
        return goal && result ? `目标：${goal}｜结果：${result}` : (goal || result);
      } catch { return ""; }
    }).filter(Boolean);
  } catch {
    return [];
  }
}

// 记忆注入块（长期 + 短期）
function buildMemoryBlock(sessionId, { longTerm = 3, shortTerm = 3 } = {}) {
  if (!sessionId) return "";
  const parts = [];
  const longLines = getLongTermMemoryLines(sessionId, longTerm);
  if (longLines.length) {
    parts.push(`【长期记忆 · 以往任务经验】\n${longLines.map((l) => `- ${l}`).join("\n")}`);
  }
  const shortLines = getShortTermMemoryLines(sessionId, shortTerm);
  if (shortLines.length) {
    parts.push(`【短期记忆 · 本会话近期进展】\n${shortLines.map((l) => `- ${l}`).join("\n")}`);
  }
  return parts.join("\n\n");
}

// 统一组装系统提示词：用户强制提示词 + 已启用技能/插件 + 长短期记忆 + 跨会话画像
function buildEffectiveSystemPrompt(agentPrompt, sessionId) {
  const parts = [promptStore.buildSystemPrompt(agentPrompt || "", "global")];
  const storeBlock = getEnabledStorePrompt(db);
  if (storeBlock) parts.push(storeBlock);
  const memBlock = buildMemoryBlock(sessionId);
  if (memBlock) parts.push(memBlock);
  const profileBlock = userProfileService.buildPromptBlock();
  if (profileBlock) parts.push(profileBlock);
  return parts.filter(Boolean).join("\n\n");
}

// 1. 主动服务模式（真实分析 + 真实群聊执行）
async function runProactiveService(res, options) {
  const { sessionId, message, model, permissionMode, cwd, images = null, allowWrites = false } = options;
  const { ProactiveServiceEngine } = await import('./engine/proactive-service.mjs');
  const engine = new ProactiveServiceEngine(db, { sessionId });
  const startedAt = new Date().toISOString();
  
  sendSSE(res, { type: "proactive_init", sessionId, message: "主动服务引擎已启动" });
  
  // 分析用户行为并提供主动服务
  let suggestion = null;
  try {
    suggestion = await engine.provideProactiveService(sessionId);
  } catch (e) {
    console.warn("[Proactive] 建议生成失败:", e?.message || e);
  }
  if (suggestion) {
    sendSSE(res, {
      type: "proactive_suggestion",
      suggestion: suggestion.message,
      action: suggestion.action,
      priority: suggestion.priority
    });
  }
  
  // 真实群聊执行
  await runGroupChat(res, {
    sessionId, message, model,
    intentMode: "auto", maxAgents: 3, enableSummary: true,
    permissionMode, cwd, images, allowWrites
  });

  // 主动服务执行摘要（让用户看到真实执行量）
  let toolCalls = 0;
  try {
    toolCalls = db.prepare("SELECT COUNT(*) AS c FROM audit_logs WHERE created_at >= ?").get(startedAt)?.c || 0;
  } catch { /* 忽略 */ }
  sendSSE(res, {
    type: "proactive_summary",
    suggestion: suggestion?.message || "本轮未产生新的主动建议",
    toolCalls,
    message: `主动服务执行完毕：本轮真实工具调用 ${toolCalls} 次。${suggestion?.message ? `建议：${suggestion.message}` : ""}`
  });
  
  sendSSE(res, { type: "proactive_complete", sessionId });
}

// 2. 持续对话模式
async function runContinuousDialogue(res, options) {
  const { sessionId, message, model, permissionMode, cwd, images = null, allowWrites = false } = options;
  const { ContinuousDialogueManager } = await import('./engine/proactive-service.mjs');
  const manager = new ContinuousDialogueManager(db);
  
  sendSSE(res, { type: "continuous_init", sessionId, message: "持续对话模式已启动" });
  
  // 获取活跃任务
  const activeTasks = manager.getActiveTasks(sessionId);
  
  if (activeTasks.length > 0) {
    sendSSE(res, {
      type: "active_tasks",
      tasks: activeTasks.map(t => ({ id: t.id, name: t.task_name, progress: t.progress }))
    });
  }
  
  // 检查是否需要创建新任务
  if (message.includes('创建任务') || message.includes('新任务')) {
    const taskName = message.replace(/创建任务|新任务/g, '').trim();
    const taskId = manager.createTask(sessionId, { name: taskName, description: message });
    sendSSE(res, { type: "task_created", taskId, taskName });
  }
  
  // 真实群聊执行
  await runGroupChat(res, {
    sessionId,
    message,
    model,
    intentMode: "auto",
    maxAgents: 3,
    enableSummary: true,
    permissionMode,
    cwd,
    images,
    allowWrites
  });
  
  // 检查任务完成
  const completed = manager.checkTaskCompletion(sessionId);
  if (completed.length > 0) {
    sendSSE(res, { type: "tasks_completed", tasks: completed });
  }
  
  sendSSE(res, { type: "continuous_complete", sessionId });
}

// 3. 创作协作模式（真实多轮创作）
async function runCreativeCollaboration(res, options) {
  const { sessionId, message, model, permissionMode, cwd, images = null, allowWrites = false } = options;
  
  sendSSE(res, { type: "creative_init", sessionId, message: "创作协作模式已启动（真实创作）" });
  
  const creativeTypes = ['文章', '故事', '诗歌', '代码', '设计', '方案'];
  const detectedType = creativeTypes.find(t => message.includes(t)) || '通用创作';
  sendSSE(res, { type: "creative_type", creativeType: detectedType });
  
  const agents = listAgents(true);
  const creativeAgent = agents.find(a => (a.role || "").includes("创作") || (a.role || "").includes("设计")) || agents[0];
  const context = buildDirectContext({ cwd, permissionMode, allowWrites, memoryLines: getLongTermMemoryLines(sessionId).join("\n") });
  
  const rounds = [
    { name: "创意构思", prompt: `请为以下主题产出创意构思（3-5 个方向）：${message}` },
    { name: "大纲设计", prompt: `基于用户主题设计详细大纲：${message}` },
    { name: "内容创作", prompt: `按照大纲全身心创作完整内容：${message}` },
    { name: "润色优化", prompt: `对已创作内容进行润色优化并输出终稿：${message}` }
  ];
  
  let previous = "";
  for (let i = 0; i < rounds.length; i++) {
    const round = rounds[i];
    sendSSE(res, { type: "creative_round", round: i + 1, name: round.name, total: rounds.length });
    const assistantMessageId = uuidv44();
    sendSSE(res, {
      type: "agent_start", messageId: assistantMessageId,
      agentId: creativeAgent.id, agentName: creativeAgent.name, agentColor: creativeAgent.color, agentAvatar: creativeAgent.avatar,
      isSummary: false, round: i + 1
    });
    try {
      const r = await runDirectTurn(res, {
        messageId: assistantMessageId, model, agent: creativeAgent,
        prompt: previous ? `${round.prompt}\n\n【上一轮产出】\n${previous.slice(0, 2000)}` : round.prompt,
        images: null, context, maxTurns: 3, sessionId, mode: "creative"
      });
      previous = r.content;
      if (!r.content) throw new Error("空产出");
    } catch (e) {
      sendSSE(res, { type: "error", message: `${round.name}失败：${e?.message || e}` });
      break;
    }
    sendSSE(res, { type: "agent_end", messageId: assistantMessageId, agentId: creativeAgent.id, source: "direct" });
  }
  
  sendSSE(res, { type: "creative_complete", sessionId, message: "创作协作真实完成" });
}

// 4. 数据分析模式（真实分析：读取文件/调用工具）
async function runDataAnalysis(res, options) {
  const { sessionId, message, model, permissionMode, cwd, images = null, allowWrites = false } = options;
  
  sendSSE(res, { type: "analysis_init", sessionId, message: "数据分析模式已启动（真实分析）" });
  
  const dataTypes = ['销售数据', '用户数据', '运营数据', '财务数据', '市场数据'];
  const detectedType = dataTypes.find(t => message.includes(t)) || '通用数据';
  sendSSE(res, { type: "analysis_type", dataType: detectedType });
  
  const agents = listAgents(true);
  const analysisAgent = agents.find(a => a.id === "data") || agents.find(a => (a.role || "").includes("数据") || (a.role || "").includes("分析")) || agents[0];
  const context = buildDirectContext({ cwd, permissionMode, allowWrites, memoryLines: getLongTermMemoryLines(sessionId).join("\n") });
  
  const steps = [
    { name: "数据收集", prompt: `收集与任务相关的数据：先 list_directory 查看目录，再 read_file 读取相关数据文件。任务：${message}` },
    { name: "数据分析", prompt: `基于上一步收集到的数据做实际分析（统计、对比、找规律）。任务：${message}` },
    { name: "报告生成", prompt: `基于真实分析结果输出结构化分析报告（结论+关键数据+建议）。任务：${message}` }
  ];
  
  for (let i = 0; i < steps.length; i++) {
    const step = steps[i];
    sendSSE(res, { type: "analysis_step", step: i + 1, name: step.name, description: step.name });
    const assistantMessageId = uuidv44();
    sendSSE(res, {
      type: "agent_start", messageId: assistantMessageId,
      agentId: analysisAgent.id, agentName: analysisAgent.name, agentColor: analysisAgent.color, agentAvatar: analysisAgent.avatar,
      isSummary: false, round: i + 1
    });
    try {
      await runDirectTurn(res, {
        messageId: assistantMessageId, model, agent: analysisAgent,
        prompt: step.prompt, images: null, context, maxTurns: 5, sessionId, mode: "analysis"
      });
    } catch (e) {
      sendSSE(res, { type: "error", message: `${step.name}失败：${e?.message || e}` });
      break;
    }
    sendSSE(res, { type: "agent_end", messageId: assistantMessageId, agentId: analysisAgent.id, source: "direct" });
  }
  
  sendSSE(res, { type: "analysis_complete", sessionId, message: "数据分析真实完成" });
}

// 自主步骤评分：六档 0 / 0.2 / 0.4 / 0.6 / 0.8 / 1
// 0=失败或空谈；0.2=仅汇报；0.4=1 次只读；0.6=≥2 次只读；
// 0.8=1 次写入；1=写入+复验（同一步含读取）或 ≥2 次写入
const WRITE_TOOL_NAMES = ['write_file', 'edit_file', 'execute_command', 'add_knowledge'];
function scoreAutonomousStep({ tools = [], isReportStep = false, failed = false }) {
  if (failed) return 0;
  if (tools.length === 0) return isReportStep ? 0.2 : 0;
  const writes = tools.filter((t) => WRITE_TOOL_NAMES.includes(t)).length;
  const reads = tools.length - writes;
  if (writes >= 2) return 1;
  if (writes === 1) return reads > 0 ? 1 : 0.8;
  if (reads >= 2) return 0.6;
  return 0.4;
}

// 保存自主任务检查点（每步完成后调用）
function saveAutonomousTaskProgress(taskId, currentStep, stepResults) {
  if (!taskId) return;
  try {
    db.prepare("UPDATE autonomous_tasks SET step_results = ?, current_step = ?, status = 'running', updated_at = ? WHERE id = ?")
      .run(JSON.stringify(stepResults), currentStep, new Date().toISOString(), taskId);
  } catch (e) {
    console.warn("[Autonomous] 检查点保存失败(忽略):", e?.message || e);
  }
}

// 5. 自主代理模式：自规划（模型生成步骤）→ 逐步真实执行（模型+工具）→ 学习（记录沉淀）→ 进化（经验总结）
async function runAutonomousAgent(res, options) {
  const { sessionId, message, model, permissionMode, cwd, images = null, allowWrites = false, resumeTaskId = null } = options;

  // 断点续跑：加载未完成任务
  let resumeTask = null;
  if (resumeTaskId) {
    resumeTask = db.prepare("SELECT * FROM autonomous_tasks WHERE id = ?").get(String(resumeTaskId));
    if (!resumeTask) {
      sendSSE(res, { type: "error", message: `未找到可续跑任务: ${resumeTaskId}` });
      return;
    }
    if (resumeTask.status === "completed") {
      sendSSE(res, { type: "error", message: "该任务已完成，无需续跑" });
      return;
    }
  }
  const goalText = resumeTask ? resumeTask.goal : message;
  // 队列任务超时保护：在步骤边界检查，避免单个任务长期占用调度器
  const timeoutSec = resumeTask
    ? Math.min(Math.max(Number(resumeTask.timeout_sec) || 900, 60), 7200)
    : 0;
  const deadline = resumeTask
    ? new Date(resumeTask.started_at || Date.now()).getTime() + timeoutSec * 1000
    : 0;

  sendSSE(res, { type: "autonomous_init", sessionId, message: resumeTask ? "自主代理任务从检查点继续" : "自主代理模式已启动（真实执行）" });
  
  const agents = listAgents(true);
  const executor = agents.find(a => a.id === "tech") || agents.find(a => (a.role || "").includes("执行") || (a.role || "").includes("排查")) || agents[0];
  const context = buildDirectContext({ cwd, permissionMode, allowWrites, memoryLines: getLongTermMemoryLines(sessionId).join("\n") });
  const learner = learningEngine;
  const memory = hierarchicalMemory;
  // 历史经验与记忆参与规划（进化经验真实回流）
  const learningStats = learner.analyzeLearning(executor.id, "autonomous");
  const planMemoryBlock = buildMemoryBlock(sessionId, { longTerm: 3, shortTerm: 5 }) +
    `\n历史执行样本：${learningStats.totalRecords} 条，平均得分 ${Number(learningStats.avgScore || 0).toFixed(2)}。`;
  
  let subtasks = [];
  let stepResults = [];
  let startIndex = 0;
  let taskId = null;
  let replanCount = 0;
  let needPlan = !resumeTask;

  if (resumeTask) {
    try { subtasks = JSON.parse(resumeTask.plan || "[]"); } catch { subtasks = []; }
    try { stepResults = JSON.parse(resumeTask.step_results || "[]"); } catch { stepResults = []; }
    if (!Array.isArray(stepResults)) stepResults = [];
    replanCount = Number(resumeTask.replan_count) || 0;
    taskId = resumeTask.id;
    if (!Array.isArray(subtasks) || subtasks.length === 0) {
      // 队列任务尚未规划：按新任务生成计划，沿用原任务 id，保证排队记录连续
      needPlan = true;
      db.prepare("UPDATE autonomous_tasks SET status = 'running', last_error = NULL, updated_at = ? WHERE id = ?")
        .run(new Date().toISOString(), taskId);
    } else {
      startIndex = Math.min(Number(resumeTask.current_step) || 0, subtasks.length);
      db.prepare("UPDATE autonomous_tasks SET status = 'running', last_error = NULL, updated_at = ? WHERE id = ?")
        .run(new Date().toISOString(), taskId);
      sendSSE(res, {
        type: "autonomous_resumed", taskId,
        currentStep: startIndex, totalSteps: subtasks.length, goal: goalText
      });
      sendSSE(res, { type: "autonomous_subtasks", subtasks });
    }
  }

  if (needPlan) {
    // ---- 第一步：模型自主规划（生成步骤 JSON），并参考历史高分策略 ----
    sendSSE(res, { type: "autonomous_planning", message: "模型正在自主规划任务..." });
    const strategies = strategyLibrary.matchForGoal(goalText, 2);
    const matchedStrategyIds = strategies.map(s => s.id);
    const strategyHints = strategies.length > 0
      ? `【可复用高分策略 · 参考其后按当前任务调整】\n` +
        strategies.map((s, i) => `${i + 1}. ${s.name}（历史均分 ${Number(s.avgScore || 0).toFixed(2)}）：${(s.steps || []).map((x) => x.name || x).join(" → ")}`).join("\n")
      : "";
    if (strategies.length > 0) {
      sendSSE(res, {
        type: "autonomous_strategy",
        message: `命中 ${strategies.length} 条历史高分策略，已参与规划`,
        strategies: strategies.map((s) => ({ id: s.id, name: s.name, avgScore: s.avgScore }))
      });
    }
    // 记录任务开始时间与匹配的策略 ID，用于后续效果回归
    const taskStartTime = Date.now();
    if (matchedStrategyIds.length > 0) {
      try {
        db.prepare("UPDATE autonomous_tasks SET matched_strategy_ids = ?, task_start_time = ? WHERE id = ?")
          .run(JSON.stringify(matchedStrategyIds), taskStartTime, taskId);
      } catch { /* 忽略：表可能未迁移 */ }
    }
    try {
      subtasks = await requestAutonomousPlan({
        goalText, model, cwd,
        memoryBlock: planMemoryBlock,
        strategyHints
      });
    } catch (e) {
      console.warn("[Autonomous] 模型规划失败，使用兜底拆分:", e?.message || e);
    }
    if (!subtasks || subtasks.length === 0) {
      subtasks = [
        { name: "任务分析与拆解", action: "分析用户需求并列出关键动作" },
        { name: "执行落地", action: "使用工具实际执行任务" },
        { name: "验证与总结", action: "检查结果并总结" }
      ];
    }
    const now = new Date().toISOString();
    if (resumeTask) {
      db.prepare(`
        UPDATE autonomous_tasks SET plan = ?, total_steps = ?, current_step = 0, status = 'running', updated_at = ?
        WHERE id = ?
      `).run(JSON.stringify(subtasks), subtasks.length, now, taskId);
    } else {
      taskId = `at-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      db.prepare(`
        INSERT INTO autonomous_tasks (id, session_id, goal, plan, step_results, total_steps, current_step, status, last_error, created_at, updated_at, completed_at)
        VALUES (?, ?, ?, ?, ?, ?, 0, 'running', NULL, ?, ?, NULL)
      `).run(taskId, sessionId, goalText, JSON.stringify(subtasks), JSON.stringify([]), subtasks.length, now, now);
    }
    sendSSE(res, { type: "autonomous_task_created", taskId, totalSteps: subtasks.length, goal: goalText });
    sendSSE(res, { type: "autonomous_subtasks", subtasks });
    appendJournal({
      taskId, sessionId, eventType: "task_plan",
      summary: `规划 ${subtasks.length} 步：${subtasks.map((s) => s.name).join(" → ")}`,
      details: { subtasks }
    });
  }
  
  // ---- 第二步：逐步真实执行（支持从检查点续跑） ----
  let turnSummaries = stepResults.filter(Boolean).map(r => `${r.name}: ${String(r.output || '').slice(0, 120)}`);
  let verifiedSteps = stepResults.filter(r => r && r.verified).length;
  
  try {
  for (let i = startIndex; i < subtasks.length; i++) {
    if (deadline && Date.now() > deadline) {
      throw new Error(`任务执行超时（超过 ${Math.round(timeoutSec / 60)} 分钟）`);
    }
    const subtask = subtasks[i];
    sendSSE(res, { type: "autonomous_executing", subtask: i + 1, name: subtask.name });
    const assistantMessageId = uuidv44();
    sendSSE(res, {
      type: "agent_start", messageId: assistantMessageId,
      agentId: executor.id, agentName: executor.name, agentColor: executor.color, agentAvatar: executor.avatar,
      isSummary: false, round: i + 1
    });
    const stepPrompt =
      `【自主任务 · 第 ${i + 1}/${subtasks.length} 步】${subtask.name}\n` +
      `${subtask.action}\n\n` +
      `【用户总目标】${goalText}\n` +
      (turnSummaries.length > 0 ? `【已完成步骤】\n${turnSummaries.map((s, k) => `${k + 1}. ${s}`).join("\n")}\n\n` : "") +
      `【硬性要求】这一步必须用工具产出真实结果：要创建目录就调 write_file（带路径即自动建目录），要写文档就 write_file 写完整内容，要整理知识库就调 add_knowledge 新增条目。禁止只描述计划不动手——没有真实产出的步骤视为失败。执行完后报告：产出了哪些文件/条目（给出路径或标题）。`;
    let stepContent = "";
    try {
      const r = await runDirectTurn(res, {
        messageId: assistantMessageId, model, agent: executor, prompt: stepPrompt,
        images: null, context, maxTurns: 5, sessionId, mode: "autonomous"
      });
      stepContent = r.content;
      // ---- 验证：真实工具调用才算有效执行（纯汇报类步骤不强制调工具）----
      const toolNames = (r.usedTools || []).map((t) => t.name);
      const verified = toolNames.length > 0;
      const isReportStep = /报告|总结|汇总|说明/.test(`${subtask.name} ${subtask.action}`);
      const score = scoreAutonomousStep({ tools: toolNames, isReportStep, failed: false });
      if (verified) verifiedSteps++;
      sendSSE(res, {
        type: "autonomous_step_verify",
        subtask: i + 1, name: subtask.name,
        verified, isReportStep, score, toolCount: toolNames.length, tools: toolNames
      });
      // ---- 学习：沉淀本次执行（六档评分 0 / 0.2 / 0.4 / 0.6 / 0.8 / 1）----
      learner.recordLearning(
        executor.id, "autonomous",
        { step: subtask.name, goal: goalText },
        { output: stepContent.slice(0, 500) },
        { tools: toolNames, verified, isReportStep: Boolean(isReportStep) },
        score
      );
      turnSummaries.push(`${subtask.name}: ${stepContent.slice(0, 120)}`);
      // 保存检查点
      stepResults[i] = {
        step: i + 1, name: subtask.name, verified, score,
        output: stepContent.slice(0, 500)
      };
      saveAutonomousTaskProgress(taskId, i + 1, stepResults);
      appendJournal({
        taskId, sessionId, eventType: "step_done",
        summary: `第 ${i + 1} 步完成：${subtask.name}（评分 ${score.toFixed(1)}）`,
        details: { step: i + 1, name: subtask.name, score, tools: toolNames, output: stepContent.slice(0, 800) },
        artifacts: { tools: toolNames }
      });
      sendSSE(res, {
        type: "autonomous_checkpoint",
        taskId, currentStep: i + 1, totalSteps: subtasks.length, status: "running"
      });
      sendSSE(res, { type: "autonomous_learning", message: `步骤${i + 1}执行数据已沉淀（评分 ${score.toFixed(1)}）` });
    } catch (e) {
      stepContent = `步骤执行失败：${e?.message || e}`;
      sendSSE(res, { type: "autonomous_exec_error", subtask: i + 1, name: subtask.name, error: e?.message || String(e) });
      learner.recordLearning(executor.id, "autonomous", { step: subtask.name, goal: goalText }, { output: "失败" }, { error: e?.message, verified: false }, 0);
      stepResults[i] = {
        step: i + 1, name: subtask.name, verified: false, score: 0,
        output: "失败", error: e?.message || String(e)
      };
      saveAutonomousTaskProgress(taskId, i + 1, stepResults);
      appendJournal({
        taskId, sessionId, eventType: "step_failed",
        summary: `第 ${i + 1} 步失败：${subtask.name}`,
        details: { step: i + 1, name: subtask.name, error: e?.message || String(e) }
      });
      sendSSE(res, {
        type: "autonomous_checkpoint",
        taskId, currentStep: i + 1, totalSteps: subtasks.length, status: "running"
      });
      // ---- 失败重规划：由模型修订剩余计划（每个任务最多 2 次）----
      if (i < subtasks.length - 1 && replanCount < 2) {
        try {
          let revised = await requestAutonomousPlan({
            goalText, model, cwd,
            memoryBlock: planMemoryBlock,
            replanContext:
              `你是自主任务的失败修复规划器。\n` +
              `总目标：${goalText}\n` +
              `已完成步骤：${turnSummaries.length ? turnSummaries.join("；") : "无"}\n` +
              `失败步骤：${subtask.name}（错误：${e?.message || e}）\n` +
              `原剩余步骤：${subtasks.slice(i + 1).map((s) => s.name).join(" → ") || "无"}\n\n` +
              `请重新规划剩余 1-3 个步骤，绕开上述失败原因；只输出 JSON 数组，每项格式 {"name":"步骤名","action":"该步骤要做什么"}`
          });
          // 模型未返回有效计划时兜底：换一种方式重试当前步骤
          if (!revised || revised.length === 0) {
            revised = [{
              name: "重试并完成任务",
              action: `${subtask.action}（上次失败原因：${e?.message || e}，请换一种工具或路径完成）`
            }];
          }
          if (revised && revised.length > 0) {
            replanCount++;
            subtasks = [...subtasks.slice(0, i + 1), ...revised];
            db.prepare("UPDATE autonomous_tasks SET plan = ?, total_steps = ?, replan_count = ?, updated_at = ? WHERE id = ?")
              .run(JSON.stringify(subtasks), subtasks.length, replanCount, new Date().toISOString(), taskId);
            appendJournal({
              taskId, sessionId, eventType: "replan",
              summary: `第 ${i + 1} 步失败，重规划 ${revised.length} 步（第 ${replanCount} 次）`,
              details: { afterStep: i + 1, replanCount, subtasks: revised }
            });
            sendSSE(res, {
              type: "autonomous_replanned",
              afterStep: i + 1,
              replanCount,
              totalSteps: subtasks.length,
              message: `第 ${i + 1} 步失败，已重规划剩余 ${revised.length} 步`,
              subtasks
            });
          }
        } catch (replanError) {
          console.warn("[Autonomous] 失败重规划未生效(忽略):", replanError?.message || replanError);
        }
      }
    }
    sendSSE(res, { type: "agent_end", messageId: assistantMessageId, agentId: executor.id, source: stepContent ? "direct" : "error" });
  }
  } catch (e) {
    // 未预期异常：保留检查点，标记暂停，可续跑
    try {
      db.prepare("UPDATE autonomous_tasks SET status = 'paused', last_error = ?, updated_at = ? WHERE id = ?")
        .run(e?.message || String(e), new Date().toISOString(), taskId);
    } catch { /* 忽略 */ }
    appendJournal({
      taskId, sessionId, eventType: "task_paused",
      summary: "任务暂停，可从检查点续跑",
      details: { error: e?.message || String(e) }
    });
    sendSSE(res, { type: "autonomous_paused", taskId, error: e?.message || String(e), message: "任务已暂停，可从检查点续跑" });
    return;
  }
  
  // ---- 第三步：进化（经验总结 → 长期记忆）----
  const analysis = learner.analyzeLearning(executor.id, "autonomous");
  const evolutionNote =
    `任务「${goalText.slice(0, 60)}」执行完毕：共 ${subtasks.length} 步，其中 ${verifiedSteps} 步通过工具验证；历史同类任务平均分 ${Number(analysis.avgScore || 0).toFixed(2)}，累计学习样本 ${analysis.totalRecords} 条。`;
  memory.storeLongTermMemory(sessionId, { summary: evolutionNote, goal: goalText, steps: turnSummaries.map((s, k) => ({ step: k + 1, note: s })) }, 5);
  appendJournal({
    taskId, sessionId, eventType: "task_completed",
    summary: evolutionNote,
    details: { totalSteps: subtasks.length, verifiedSteps, goal: goalText },
    artifacts: { steps: turnSummaries }
  });
  sendSSE(res, {
    type: "autonomous_evolved",
    message: evolutionNote,
    improvements: (analysis.improvements || []).slice(0, 3)
  });

  // 标记任务完成
  try {
    const now = new Date().toISOString();
    db.prepare("UPDATE autonomous_tasks SET status = 'completed', current_step = ?, updated_at = ?, completed_at = ? WHERE id = ?")
      .run(subtasks.length, now, now, taskId);
  } catch { /* 忽略 */ }

  // ---- 策略效果回归：记录复用策略的执行结果 ----
  try {
    const taskRow = db.prepare("SELECT matched_strategy_ids, task_start_time FROM autonomous_tasks WHERE id = ?").get(taskId);
    if (taskRow?.matched_strategy_ids) {
      const matchedIds = JSON.parse(taskRow.matched_strategy_ids);
      const startTime = Number(taskRow.task_start_time) || Date.now();
      const durationMs = Date.now() - startTime;
      // 计算任务平均评分（仅已验证步骤）
      const results = safeJson(db.prepare("SELECT step_results FROM autonomous_tasks WHERE id = ?").get(taskId)?.step_results || '[]', []);
      const verifiedResults = results.filter(r => r && r.verified);
      const scores = verifiedResults.map(r => Number(r.score || 0));
      const avgScore = scores.length > 0 ? scores.reduce((a, b) => a + b, 0) / scores.length : 0;
      const success = verifiedResults.length > 0 && avgScore >= 0.6; // 认为成功：有验证步骤且均分>=0.6
      
      for (const sid of matchedIds) {
        strategyLibrary.recordReuseResult(sid, { success, score: avgScore, durationMs });
      }
    }
  } catch (e) {
    console.warn("[Autonomous] 策略效果回归记录失败(忽略):", e?.message || e);
  }

  // ---- 策略沉淀：高分执行路径固化为可复用技能 ----
  try {
    const captured = strategyLibrary.captureFromTask(taskId);
    if (captured) {
      sendSSE(res, {
        type: "autonomous_strategy_saved",
        message: `高分执行路径已沉淀为可复用策略「${captured.name}」（均分 ${Number(captured.avgScore || 0).toFixed(2)}）`,
        strategyId: captured.id
      });
    }
  } catch (e) {
    console.warn("[Autonomous] 策略沉淀失败(忽略):", e?.message || e);
  }

  // ---- 长程目标：回算进度并触发验收 ----
  try {
    const taskRow = db.prepare("SELECT goal_id FROM autonomous_tasks WHERE id = ?").get(taskId);
    if (taskRow?.goal_id) {
      goalService.refreshProgress(taskRow.goal_id);
      setTimeout(() => {
        goalService.review(taskRow.goal_id).catch((e) => console.warn("[Goal] 自动验收失败(忽略):", e?.message || e));
      }, 800).unref?.();
    }
  } catch { /* 忽略 */ }

  sendSSE(res, { type: "autonomous_task_completed", taskId, totalSteps: subtasks.length });
  
  sendSSE(res, { type: "autonomous_complete", sessionId, message: "自主代理任务已真实执行完成" });
}

// 6. 跨平台模式（真实生成各平台适配内容）
async function runCrossPlatform(res, options) {
  const { sessionId, message, model, permissionMode, cwd, images = null, allowWrites = false } = options;
  
  sendSSE(res, { type: "cross_platform_init", sessionId, message: "跨平台模式已启动（真实生成）" });
  
  // 检测目标平台
  const platforms = ['微信', '钉钉', '企业微信', 'Slack', 'Teams', '邮件'];
  const detectedPlatforms = platforms.filter(p => message.includes(p));
  
  sendSSE(res, { type: "cross_platform_targets", platforms: detectedPlatforms });
  
  const agents = listAgents(true);
  const platformAgent = agents.find(a => (a.role || "").includes("平台") || (a.role || "").includes("集成")) || agents[0];
  const context = buildDirectContext({ cwd, permissionMode, allowWrites, memoryLines: getLongTermMemoryLines(sessionId).join("\n") });
  
  // 未指明平台时默认按通用社交平台生成
  const targets = detectedPlatforms.length > 0 ? detectedPlatforms : ['通用'];
  for (const platform of targets) {
    sendSSE(res, { type: "cross_platform_processing", platform });
    const assistantMessageId = uuidv44();
    sendSSE(res, {
      type: "agent_start", messageId: assistantMessageId,
      agentId: platformAgent.id, agentName: platformAgent.name, agentColor: platformAgent.color, agentAvatar: platformAgent.avatar,
      isSummary: false, round: 1
    });
    try {
      await runDirectTurn(res, {
        messageId: assistantMessageId, model, agent: platformAgent,
        prompt: `请针对「${platform}」平台，把下面的诉求改写成适合该平台发布的真实内容（注意平台风格与格式差异）：\n\n${message}`,
        images: null, context, maxTurns: 3, sessionId, mode: "cross_platform"
      });
    } catch (e) {
      sendSSE(res, { type: "error", message: `${platform} 处理失败：${e?.message || e}` });
      break;
    }
    sendSSE(res, { type: "agent_end", messageId: assistantMessageId, agentId: platformAgent.id, source: "direct" });
  }
  
  sendSSE(res, { type: "cross_platform_complete", sessionId, message: "跨平台处理真实完成" });
}

let gbkEncoder = null;
function resolveLogEncoding() {
  const pref = String(process.env.LOG_ENCODING || "auto").trim().toLowerCase();
  if (pref === "utf8" || pref === "utf-8") return "utf8";
  if (pref === "gbk" || pref === "gb2312" || pref === "ansi" || pref === "936") return "gbk";
  let locale = "";
  try {
    locale = Intl.DateTimeFormat().resolvedOptions().locale || "";
  } catch {
  }
  return /^zh\b/i.test(locale) ? "gbk" : "utf8";
}
function encodeLogText(text) {
  if (resolveLogEncoding() === "utf8") return Buffer.from(text, "utf-8");
  if (gbkEncoder) {
    try {
      return gbkEncoder.encode(text, "gbk");
    } catch {
    }
  }
  return Buffer.from(text, "utf-8");
}
function appendLogFile(file, text) {
  try {
    if (!fs2.existsSync(logDir)) fs2.mkdirSync(logDir, { recursive: true });
    fs2.appendFileSync(path2.join(logDir, file), encodeLogText(text + "\n"));
  } catch {
  }
}
function formatLogArgs(args) {
  return args.map((a) => {
    if (typeof a === "string") return a;
    if (a instanceof Error) return a.stack || a.message;
    try {
      return typeof a === "object" ? JSON.stringify(a) : String(a);
    } catch {
      return String(a);
    }
  }).join(" ");
}
function installConsoleTee() {
  const stamp = () => {
    const d = /* @__PURE__ */ new Date();
    const p = (n, w = 2) => String(n).padStart(w, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  };
  for (const level of ["log", "info", "warn", "error"]) {
    const orig = console[level] ? console[level].bind(console) : () => {
    };
    const toStderr = level === "error" || level === "warn";
    console[level] = (...args) => {
      const text = formatLogArgs(args);
      if (toStderr ? process.stderr.isTTY : process.stdout.isTTY) {
        try {
          orig(text);
        } catch {
        }
      }
      const file = toStderr ? "server.err.log" : "server.log";
      const at = stamp();
      const lines = text.split("\n");
      const body = lines.map((l, i) => (i === 0 ? `[${at}] ${l}` : l)).join("\n");
      appendLogFile(file, body);
    };
  }
}
function loadIconvForLog() {
}
async function initLogEncoder() {
  if (resolveLogEncoding() !== "gbk") return;
  try {
    const mod = await import("iconv-lite");
    gbkEncoder = mod.default || mod;
  } catch {
    console.warn("[Log] iconv-lite 不可用，日志回退为 UTF-8（在记事本中打开可能显示乱码）");
  }
}
function getBindHost() {
  const h = String(process.env.BIND_HOST || "127.0.0.1").trim();
  return h || "127.0.0.1";
}
function isLoopbackHost(host) {
  const h = String(host || "").trim().toLowerCase().replace(/^\[|\]$/g, "");
  return h === "127.0.0.1" || h === "localhost" || h === "::1" || h === "0:0:0:0:0:0:0:1" || h === "";
}
function adminAuthRequired() {
  const explicit = String(process.env.ADMIN_AUTH || process.env.REQUIRE_ADMIN_AUTH || "auto").trim().toLowerCase();
  if (["1", "on", "true", "yes", "required", "enable", "enabled"].includes(explicit)) return true;
  if (["0", "off", "false", "no", "none", "disable", "disabled"].includes(explicit)) return false;
  return !isLoopbackHost(getBindHost());
}
function adminToken() {
  const secret = String(process.env.ADMIN_PASSWORD || "");
  return crypto.createHmac("sha256", "agent-squad-admin-session-v1").update(secret).digest("hex");
}
function ensureAdminPassword() {
  if (!adminAuthRequired()) return null;
  if (String(process.env.ADMIN_PASSWORD || "").trim()) return null;
  const generated = crypto.randomBytes(9).toString("base64url");
  process.env.ADMIN_PASSWORD = generated;
  try {
    const envPath = path2.join(__dirname2, "..", ".env");
    const lines = fs2.existsSync(envPath) ? fs2.readFileSync(envPath, "utf-8").split(/\r?\n/) : [];
    const idx = lines.findIndex((l) => l.trim().startsWith("ADMIN_PASSWORD="));
    const newLine = `ADMIN_PASSWORD=${generated}`;
    if (idx >= 0) lines[idx] = newLine;
    else lines.push(newLine);
    fs2.writeFileSync(envPath, lines.join("\n").replace(/\n+$/, "") + "\n", "utf-8");
  } catch (e) {
    console.warn("[Admin] 无法把自动生成的管理密码写入 .env(仅本次运行有效):", e?.message || e);
  }
  return generated;
}
function readCookie(req, name) {
  const raw = String(req.headers.cookie || "");
  for (const part of raw.split(/;\s*/)) {
    const eq = part.indexOf("=");
    if (eq > 0 && part.slice(0, eq).trim() === name) {
      try {
        return decodeURIComponent(part.slice(eq + 1));
      } catch {
        return null;
      }
    }
  }
  return null;
}
function isAdminAuthed(req) {
  if (!adminAuthRequired()) return true;
  const expected = adminToken();
  if (readCookie(req, "admin_token") === expected) return true;
  const auth = String(req.headers.authorization || "");
  if (auth.toLowerCase().startsWith("bearer ")) {
    const token = auth.slice(7).trim();
    if (token && token === expected) return true;
  }
  return false;
}
function requireAdmin(req, res, next) {
  if (!adminAuthRequired()) return next();
  if (isAdminAuthed(req)) return next();
  return res.status(401).json({ error: "未授权：请先打开 /admin 输入管理密码登录" });
}
function adminCookieHeader() {
  return `admin_token=${encodeURIComponent(adminToken())}; Path=/; HttpOnly; SameSite=Strict; Max-Age=86400`;
}
function buildAdminLoginPage() {
  const err = "";
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>管理后台登录 · 智能 Agent 群</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         font-family:"Microsoft YaHei","PingFang SC","Segoe UI",system-ui,sans-serif;
         background:#f3f5f9; color:#1f2329; }
  .card { width:100%; max-width:380px; background:#fff; border-radius:14px; padding:32px 30px;
          box-shadow:0 8px 30px rgba(31,35,41,.10); }
  h1 { font-size:19px; margin:0 0 6px; }
  p.tip { font-size:13px; color:#646a73; margin:0 0 22px; line-height:1.6; }
  label { display:block; font-size:13px; margin-bottom:7px; color:#1f2329; }
  input { width:100%; height:40px; padding:0 12px; font-size:14px; border:1px solid #dee0e3;
          border-radius:7px; outline:none; }
  input:focus { border-color:#0052d9; box-shadow:0 0 0 3px rgba(0,82,217,.10); }
  button { margin-top:18px; width:100%; height:40px; border:0; border-radius:7px; background:#0052d9;
           color:#fff; font-size:14px; cursor:pointer; }
  button:hover { background:#0044b8; }
  .err { margin-top:14px; font-size:13px; color:#d54941; min-height:18px; }
  .foot { margin-top:20px; font-size:12px; color:#8f959e; line-height:1.7; }
  code { background:#f2f3f5; padding:1px 5px; border-radius:4px; font-size:12px; }
</style>
</head>
<body>
  <div class="card">
    <h1>管理后台登录</h1>
    <p class="tip">本服务已对外开放，需要管理密码才能查看对话记录、知识库与统计数据。</p>
    <form id="f" method="post" action="/api/admin/login">
      <label for="pw">管理密码</label>
      <input id="pw" name="password" type="password" autocomplete="current-password" autofocus>
      <button type="submit">登 录</button>
      <div class="err" id="e">${err}</div>
    </form>
    <div class="foot">
      密码为首次启动时自动生成，已写入根目录 <code>.env</code> 的
      <code>ADMIN_PASSWORD</code>，可自行修改；若服务仅本机使用，可在 <code>.env</code> 中设置
      <code>ADMIN_AUTH=off</code> 关闭本页面。
    </div>
  </div>
<script>
  document.getElementById('f').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const pw = document.getElementById('pw').value;
    const e = document.getElementById('e');
    try {
      const r = await fetch('/api/admin/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: pw })
      });
      if (r.ok) { location.href = '/admin'; return; }
      const d = await r.json().catch(() => ({}));
      e.textContent = d.error || '\u5bc6\u7801\u9519\u8bef\uff0c\u8bf7\u91cd\u8bd5';
    } catch (ex) { e.textContent = '\u7f51\u7edc\u5f02\u5e38\uff0c\u8bf7\u91cd\u8bd5'; }
  });
</script>
</body>
</html>`;
}
function loadEnvFile() {
  const candidates = [
    path2.join(__dirname2, "..", ".env"),
    // 打包版: app/ 的上一级
    path2.join(process.cwd(), ".env")
    // 开发版: 项目根目录
  ];
  for (const envPath of candidates) {
    try {
      if (!fs2.existsSync(envPath)) continue;
      const content = fs2.readFileSync(envPath, "utf-8").replace(/^﻿/, "");
      for (const rawLine of content.split(/\r?\n/)) {
        const line = rawLine.trim();
        if (!line || line.startsWith("#")) continue;
        const eq = line.indexOf("=");
        if (eq <= 0) continue;
        const key = line.slice(0, eq).trim();
        let value = line.slice(eq + 1).trim();
        if (value.startsWith('"') && value.endsWith('"') || value.startsWith("'") && value.endsWith("'")) {
          value = value.slice(1, -1);
        }
        // .env 为最高优先级：必须覆盖外部同名环境变量，
        // 否则会被宿主/系统里遗留的同名变量（如 OPENAI_API_KEY）静默覆盖，导致配置不生效
        if (key) process.env[key] = value;
      }
      console.log(`[Env] \u5DF2\u52A0\u8F7D\u914D\u7F6E\u6587\u4EF6: ${envPath}`);
      break;
    } catch (e) {
      console.warn(`[Env] \u52A0\u8F7D ${envPath} \u5931\u8D25:`, e);
    }
  }
}
// 通用 .env 写入：更新/追加键值，同时立即写入 process.env
function writeEnvVars(vars) {
  try {
    const envPath = path2.join(__dirname2, "..", ".env");
    let lines = fs2.existsSync(envPath) ? fs2.readFileSync(envPath, "utf-8").split(/\r?\n/) : [];
    for (const [key, value] of Object.entries(vars)) {
      const idx = lines.findIndex((l) => l.trim().startsWith(`${key}=`));
      const line = `${key}=${value}`;
      if (idx >= 0) lines[idx] = line;
      else lines.push(line);
      process.env[key] = String(value);
    }
    fs2.writeFileSync(envPath, lines.filter((l) => l.trim()).join("\n") + "\n", "utf-8");
    return true;
  } catch (e) {
    console.warn("[Settings] .env 写入失败(仅内存生效):", e?.message || e);
    return false;
  }
}

function envFlag(key, fallback = "1") {
  const value = String(process.env[key] ?? fallback).trim().toLowerCase();
  return !["0", "false", "off", "no"].includes(value);
}

function getUsagePricing() {
  const num = (key) => {
    const n = Number(process.env[key]);
    return Number.isFinite(n) && n >= 0 ? n : 0;
  };
  return {
    input: num("USAGE_INPUT_PRICE"),
    output: num("USAGE_OUTPUT_PRICE"),
    cached: num("USAGE_CACHED_PRICE"),
    currency: String(process.env.USAGE_CURRENCY || "CNY").toUpperCase() === "USD" ? "USD" : "CNY"
  };
}

// 默认模型分工：default / quick / translation / vision / coding
function getModelRoutes() {
  const empty = { default: "", quick: "", translation: "", vision: "", coding: "" };
  try {
    const row = db.prepare("SELECT value FROM app_settings WHERE key = 'model_routes'").get();
    if (!row?.value) return empty;
    const parsed = JSON.parse(row.value);
    return {
      default: String(parsed.default || ""),
      quick: String(parsed.quick || ""),
      translation: String(parsed.translation || ""),
      vision: String(parsed.vision || ""),
      coding: String(parsed.coding || "")
    };
  } catch {
    return empty;
  }
}
function getModelRoute(purpose) {
  const routes = getModelRoutes();
  return routes[purpose] || "";
}
function saveModelRoutes(routes) {
  const clean = {
    default: String(routes?.default || "").slice(0, 200),
    quick: String(routes?.quick || "").slice(0, 200),
    translation: String(routes?.translation || "").slice(0, 200),
    vision: String(routes?.vision || "").slice(0, 200),
    coding: String(routes?.coding || "").slice(0, 200)
  };
  const now = new Date().toISOString();
  db.prepare(`
    INSERT INTO app_settings (key, value, updated_at) VALUES ('model_routes', ?, ?)
    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
  `).run(JSON.stringify(clean), now);
  return clean;
}

loadEnvFile();
await initLogEncoder();
installConsoleTee();
const generatedAdminPassword = ensureAdminPassword();
var app = express();
var PORT = Number(process.env.PORT) || 3e3;
var BIND_HOST = getBindHost();
app.set("trust proxy", false);
app.disable("x-powered-by");
// 必须提升 body 上限：多模态图片以 base64 随消息发送（1mb 会拒绝大图）
app.use(express.json({ limit: "10mb" }));

// ==================== 家庭访问口令（单口令，无账号体系） ====================
function getFamilyPassword() {
  return String(process.env.FAMILY_ACCESS_PASSWORD || "").trim();
}
function isLoopbackRequest(req) {
  const addr = String(req.socket?.remoteAddress || "");
  return addr === "127.0.0.1" || addr === "::1" || addr === "::ffff:127.0.0.1";
}
function familySessionToken() {
  const password = getFamilyPassword();
  if (!password) return "";
  return crypto.createHmac("sha256", password).update("ai-temple-family-session").digest("hex");
}
function parseRequestCookies(req) {
  const out = {};
  const raw = String(req.headers.cookie || "");
  for (const part of raw.split(";")) {
    const idx = part.indexOf("=");
    if (idx < 0) continue;
    out[part.slice(0, idx).trim()] = decodeURIComponent(part.slice(idx + 1).trim());
  }
  return out;
}
function isFamilyAuthenticated(req) {
  const password = getFamilyPassword();
  if (!password) return true;
  const cookie = parseRequestCookies(req).family_session || "";
  const expected = familySessionToken();
  if (!cookie || cookie.length !== expected.length) return false;
  try {
    return crypto.timingSafeEqual(Buffer.from(cookie), Buffer.from(expected));
  } catch {
    return false;
  }
}
const familyLoginAttempts = new Map();
function familyLoginAllowed(ip) {
  const now = Date.now();
  const item = familyLoginAttempts.get(ip) || { count: 0, resetAt: now + 10 * 60 * 1000 };
  if (now > item.resetAt) {
    item.count = 0;
    item.resetAt = now + 10 * 60 * 1000;
  }
  return item.count < 8;
}
function noteFamilyLoginFailure(ip) {
  const now = Date.now();
  const item = familyLoginAttempts.get(ip) || { count: 0, resetAt: now + 10 * 60 * 1000 };
  if (now > item.resetAt) {
    item.count = 0;
    item.resetAt = now + 10 * 60 * 1000;
  }
  item.count += 1;
  familyLoginAttempts.set(ip, item);
}
app.get("/api/family/status", (req, res) => {
  res.json({
    success: true,
    enabled: Boolean(getFamilyPassword()),
    authenticated: isFamilyAuthenticated(req),
    bindHost: BIND_HOST
  });
});
app.post("/api/family/login", (req, res) => {
  const password = getFamilyPassword();
  if (!password) return res.json({ success: true, enabled: false });
  const ip = req.ip || req.socket?.remoteAddress || "unknown";
  if (!familyLoginAllowed(ip)) {
    return res.status(429).json({ error: "尝试次数过多，请 10 分钟后再试" });
  }
  const input = String(req.body?.password || "");
  const a = Buffer.from(input);
  const b = Buffer.from(password);
  const ok = a.length === b.length && crypto.timingSafeEqual(a, b);
  if (!ok) {
    noteFamilyLoginFailure(ip);
    return res.status(401).json({ error: "家庭访问口令不正确" });
  }
  familyLoginAttempts.delete(ip);
  res.setHeader("Set-Cookie", `family_session=${familySessionToken()}; HttpOnly; SameSite=Lax; Path=/; Max-Age=2592000`);
  res.json({ success: true, enabled: true });
});
app.post("/api/family/logout", (req, res) => {
  res.setHeader("Set-Cookie", "family_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0");
  res.json({ success: true });
});
// 保护主应用数据接口；admin 有自己的鉴权，静态页面不拦（登录浮层需要加载）
app.use("/api", (req, res, next) => {
  if (!getFamilyPassword()) return next();
  const p = req.path;
  if (p === "/health" || p.startsWith("/family/")) return next();
  // 桌面壳只从本机轮询通知 / 投递全局划词，不要求家庭口令
  if ((p === "/desktop/notifications" || p === "/desktop/selection" || p === "/desktop/selection/ack") && isLoopbackRequest(req)) return next();
  if (p.startsWith("/admin/") || p === "/check-login" || p === "/save-env-config" || p === "/config-permission") return next();
  if (isFamilyAuthenticated(req)) return next();
  return res.status(401).json({ error: "需要家庭访问口令", code: "FAMILY_AUTH_REQUIRED" });
});

// 必须在 express.json() 之后注册，否则 POST 请求的 req.body 不会被解析
registerProviderEndpoints(app, loadEnvFile);
registerStoreEndpoints(app, db);
ensureSeedAgents();
ensureSeedFaq();
ensureFaqFts();
ensureMessageIndexes();
var defaultModel = process.env.CODEBUDDY_MODEL || "hy3";
var cachedModels = [];
console.log(`[Boot] 监听地址 ${BIND_HOST}:${PORT}（${isLoopbackHost(BIND_HOST) ? "仅本机" : "对外开放"}）`);
if (generatedAdminPassword) {
  console.log(`[Admin] 已自动生成管理密码并写入 .env 的 ADMIN_PASSWORD: ${generatedAdminPassword}`);
  console.log("[Admin] 访问 /admin 时输入该密码即可进入管理后台。");
} else if (adminAuthRequired()) {
  console.log("[Admin] 管理后台需要密码（读取 .env 中的 ADMIN_PASSWORD）。");
}
app.get("/api/health", (req, res) => {
  const cfg = isDirectProvider() ? getActiveProviderConfig() : null;
  res.json({
    status: "ok",
    provider: getAiProvider(),
    providerKey: cfg?.key || getAiProvider(),
    baseUrl: cfg?.baseUrl || normalizeOllamaBaseUrl(),
    hasApiKey: cfg ? Boolean(cfg.apiKey) : true,
    defaultModel: getDefaultModel(),
    host: BIND_HOST,
    timestamp: (/* @__PURE__ */ new Date()).toISOString()
  });
});
function maskSecret(value) {
  const s = String(value || "");
  if (s.length <= 4) return "****";
  if (s.length <= 12) return `${s.slice(0, 2)}****${s.slice(-2)}`;
  return `${s.slice(0, 4)}****${s.slice(-4)}`;
}
app.get("/api/check-login", async (req, res) => {
  const response = { isLoggedIn: false, envConfigured: false, cliConfigured: false, envVars: {}, provider: getAiProvider(), defaultModel: getDefaultModel() };
  const apiKey = process.env.CODEBUDDY_API_KEY;
  const authToken = process.env.CODEBUDDY_AUTH_TOKEN;
  if (isOllamaProvider()) {
    response.envConfigured = true;
    response.envVars.provider = "ollama";
    response.envVars.ollamaBaseUrl = normalizeOllamaBaseUrl();
    response.envVars.ollamaModel = getOllamaDefaultModel();
    try {
      const models = await getOllamaModels();
      response.isLoggedIn = true;
      response.cliConfigured = true;
      response.method = "ollama";
      response.models = models;
    } catch (error) {
      response.method = "ollama";
      response.error = error?.message || String(error);
    }
    return res.json(response);
  }
  if (apiKey || authToken) {
    response.envConfigured = true;
    if (apiKey) response.envVars.apiKey = maskSecret(apiKey);
    if (authToken) response.envVars.authToken = maskSecret(authToken);
    if (process.env.CODEBUDDY_INTERNET_ENVIRONMENT) response.envVars.internetEnv = process.env.CODEBUDDY_INTERNET_ENVIRONMENT;
    if (process.env.CODEBUDDY_BASE_URL) response.envVars.baseUrl = process.env.CODEBUDDY_BASE_URL;
  }
  try {
    let needsLogin = false;
    const result = await unstable_v2_authenticate({
      environment: "external",
      executable: process.execPath,
      onAuthUrl: async () => {
        needsLogin = true;
      }
    });
    if (!needsLogin && result?.userinfo) {
      response.isLoggedIn = true;
      response.cliConfigured = true;
      response.method = response.envConfigured ? "env" : "cli";
      console.log("[Check Login] \u5DF2\u767B\u5F55\u7528\u6237:", result.userinfo.userName);
    } else if (!needsLogin) {
      response.isLoggedIn = true;
      response.cliConfigured = true;
      response.method = response.envConfigured ? "env" : "cli";
    }
  } catch (error) {
    if (response.envConfigured) {
      response.isLoggedIn = true;
      response.method = "env";
    } else {
      response.error = error?.message || String(error);
      response.method = "none";
    }
  }
  res.json(response);
});
app.post("/api/save-env-config", requireAdmin, (req, res) => {
  const { apiKey, authToken, internetEnv, baseUrl, aiProvider, ollamaBaseUrl, ollamaModel, ollamaTimeoutMs } = req.body;
  const provider = String(aiProvider || (ollamaBaseUrl || /localhost:11434|127\.0\.0\.1:11434/i.test(String(baseUrl || "")) ? "ollama" : "codebuddy")).toLowerCase();
  if (provider !== "ollama" && !apiKey && !authToken) return res.status(400).json({ error: "\u8BF7\u81F3\u5C11\u914D\u7F6E API Key \u6216 Auth Token\uFF0C\u6216\u5C06 AI_PROVIDER \u8BBE\u4E3A ollama" });
  const configuredVars = [];
  if (provider === "ollama") {
    process.env.AI_PROVIDER = "ollama";
    process.env.OLLAMA_BASE_URL = normalizeOllamaBaseUrl(ollamaBaseUrl || baseUrl || process.env.OLLAMA_BASE_URL);
    if (ollamaModel) process.env.OLLAMA_MODEL = ollamaModel;
    if (ollamaTimeoutMs) process.env.OLLAMA_TIMEOUT_MS = String(ollamaTimeoutMs);
    configuredVars.push("AI_PROVIDER", "OLLAMA_BASE_URL");
    if (ollamaModel) configuredVars.push("OLLAMA_MODEL");
    if (ollamaTimeoutMs) configuredVars.push("OLLAMA_TIMEOUT_MS");
  } else {
    process.env.AI_PROVIDER = "codebuddy";
    configuredVars.push("AI_PROVIDER");
  }
  if (apiKey) {
    process.env.CODEBUDDY_API_KEY = apiKey;
    configuredVars.push("CODEBUDDY_API_KEY");
  }
  if (authToken) {
    process.env.CODEBUDDY_AUTH_TOKEN = authToken;
    configuredVars.push("CODEBUDDY_AUTH_TOKEN");
  }
  if (internetEnv) {
    process.env.CODEBUDDY_INTERNET_ENVIRONMENT = internetEnv;
    configuredVars.push("CODEBUDDY_INTERNET_ENVIRONMENT");
  }
  if (baseUrl && provider !== "ollama") {
    process.env.CODEBUDDY_BASE_URL = baseUrl;
    configuredVars.push("CODEBUDDY_BASE_URL");
  }
  try {
    persistEnvFile({ apiKey, authToken, internetEnv, baseUrl: provider === "ollama" ? void 0 : baseUrl, aiProvider: process.env.AI_PROVIDER, ollamaBaseUrl: process.env.OLLAMA_BASE_URL, ollamaModel: process.env.OLLAMA_MODEL, ollamaTimeoutMs: process.env.OLLAMA_TIMEOUT_MS });
  } catch (e) {
    console.warn("[Env] \u5199\u5165 .env \u5931\u8D25(\u4EC5\u5185\u5B58\u751F\u6548):", e);
  }
  cachedModels = [];
  res.json({ success: true, provider: getAiProvider(), message: `\u5DF2\u8BBE\u7F6E: ${configuredVars.join(", ")}` });
});
function persistEnvFile(vars) {
  const envPath = path2.join(__dirname2, "..", ".env");
  const keyMap = {
    AI_PROVIDER: vars.aiProvider,
    OLLAMA_BASE_URL: vars.ollamaBaseUrl,
    OLLAMA_MODEL: vars.ollamaModel,
    OLLAMA_TIMEOUT_MS: vars.ollamaTimeoutMs,
    CODEBUDDY_API_KEY: vars.apiKey,
    CODEBUDDY_AUTH_TOKEN: vars.authToken,
    CODEBUDDY_INTERNET_ENVIRONMENT: vars.internetEnv,
    CODEBUDDY_BASE_URL: vars.baseUrl
  };
  let lines = [];
  if (fs2.existsSync(envPath)) {
    lines = fs2.readFileSync(envPath, "utf-8").split(/\r?\n/);
  }
  for (const [key, value] of Object.entries(keyMap)) {
    if (!value) continue;
    const idx = lines.findIndex((l) => l.trim().startsWith(key + "="));
    const newLine = `${key}=${value}`;
    if (idx >= 0) lines[idx] = newLine;
    else lines.push(newLine);
  }
  fs2.writeFileSync(envPath, lines.filter((l, i) => !(l === "" && i === lines.length - 1)).join("\n") + "\n", "utf-8");
}
app.get("/api/ollama/health", async (req, res) => {
  try {
    const models = await getOllamaModels();
    res.json({ ok: true, provider: "ollama", baseUrl: normalizeOllamaBaseUrl(), defaultModel: getOllamaDefaultModel(), models });
  } catch (error) {
    res.status(503).json({ ok: false, provider: "ollama", baseUrl: normalizeOllamaBaseUrl(), defaultModel: getOllamaDefaultModel(), error: error?.message || String(error) });
  }
});
app.get("/api/provider", async (req, res) => {
  const cfg = isDirectProvider() ? getActiveProviderConfig() : null;
  res.json({
    provider: getAiProvider(),
    providerKey: cfg?.key || getAiProvider(),
    baseUrl: cfg?.baseUrl || normalizeOllamaBaseUrl(),
    model: cfg?.model || getDefaultModel(),
    defaultModel: getDefaultModel(),
    ollama: { baseUrl: normalizeOllamaBaseUrl(), model: getOllamaDefaultModel() }
  });
});
// 给模型列表附加能力标记（看图/文本/视频/思考/工具/嵌入）
function withCapabilities(list) {
  return (list || []).map((m) => {
    const { caps, labels } = classifyModel(m.modelId || m.name);
    return { ...m, capabilities: caps, capabilityLabels: labels };
  });
}
app.get("/api/models", async (req, res) => {
  const provider = getAiProvider();
  const cfg = isDirectProvider() ? getActiveProviderConfig() : null;
  if (req.query.refresh === "1") cachedModels = [];
  try {
    if (cachedModels.length === 0) {
      if (provider === "ollama") {
        cachedModels = await getOllamaModels();
      } else if (provider === "direct") {
        // 向提供商 /models 拉取真实模型目录
        if (cfg && cfg.baseUrl) {
          try {
            const ep = cfg.baseUrl.replace(/\/+$/, "") + "/models";
            const headers = {};
            if (cfg.apiKey) headers.Authorization = `Bearer ${cfg.apiKey}`;
            const r = await fetch(ep, { headers, signal: AbortSignal.timeout(8000) });
            if (r.ok) {
              const data = await r.json();
              if (Array.isArray(data.data)) {
                cachedModels = data.data.map((m) => ({ modelId: m.id, name: m.id }));
              }
            }
          } catch (e) {
            console.warn(`[Models] 拉取 ${cfg.key} 模型目录失败:`, e?.message || e);
          }
        }
        // 拉取失败时回退到预设字典中的模型列表
        if (cachedModels.length === 0 && cfg && Array.isArray(cfg.preset?.models) && cfg.preset.models.length > 0) {
          cachedModels = cfg.preset.models.map((m) => ({ modelId: m, name: m }));
        }
      } else {
        const session = await unstable_v2_createSession({ cwd: process.cwd(), executable: process.execPath });
        const models = await session.getAvailableModels();
        if (models && Array.isArray(models)) cachedModels = models;
      }
    }

    let resolvedDefaultModel;
    if (provider === "ollama") {
      const preferredOllamaModel = process.env.OLLAMA_MODEL || process.env.OLLAMA_DEFAULT_MODEL;
      const hasPreferred = preferredOllamaModel && cachedModels.some((m) => m.modelId === preferredOllamaModel || m.name === preferredOllamaModel);
      resolvedDefaultModel = hasPreferred ? preferredOllamaModel : cachedModels[0]?.modelId || preferredOllamaModel || getOllamaDefaultModel();
    } else if (provider === "direct") {
      resolvedDefaultModel = cfg?.model || cachedModels[0]?.modelId || cfg?.key || "default";
    } else {
      resolvedDefaultModel = defaultModel;
    }

    res.json({
      provider,
      providerKey: provider === "direct" ? cfg?.key : provider,
      models: cachedModels.length > 0 ? withCapabilities(cachedModels) : withCapabilities([{ modelId: resolvedDefaultModel, name: resolvedDefaultModel }]),
      defaultModel: resolvedDefaultModel
    });
  } catch (error) {
    const fallbackModel = getDefaultModel();
    res.json({
      provider,
      models: withCapabilities([{ modelId: fallbackModel, name: fallbackModel }]),
      defaultModel: fallbackModel,
      error: error?.message || String(error)
    });
  }
});
// ==================== 群资料与群成员（v2.2） ====================
const DEFAULT_MEMBER_PERMISSIONS = { speak: true, tools: true, write: true, command: true, notes: true };

function parseMemberPermissions(raw) {
  let obj = {};
  try { obj = typeof raw === "string" ? JSON.parse(raw || "{}") : (raw || {}); } catch { obj = {}; }
  return {
    speak: obj.speak !== false,
    tools: obj.tools !== false,
    write: obj.write !== false,
    command: obj.command !== false,
    notes: obj.notes !== false
  };
}

// 成员权限 → 需要禁用的工具名
function getMemberDisabledTools(perm) {
  const disabled = [];
  if (!perm || perm.write === false) disabled.push("write_file", "edit_file", "add_knowledge", "note_write");
  if (!perm || perm.command === false) disabled.push("execute_command");
  if (!perm || perm.notes === false) disabled.push("note_read", "note_search", "note_write");
  return [...new Set(disabled)];
}

function generateGroupNo() {
  for (let i = 0; i < 50; i += 1) {
    const no = String(Math.floor(10000000 + Math.random() * 90000000));
    if (!db.prepare("SELECT 1 FROM sessions WHERE group_no = ?").get(no)) return no;
  }
  return String(Date.now()).slice(-8);
}

function ensureSessionMembers(sessionId) {
  try {
    const count = db.prepare("SELECT COUNT(*) AS c FROM session_agents WHERE session_id = ?").get(sessionId).c;
    if (count > 0) return;
    const agents = listAgents(true);
    const now = new Date().toISOString();
    const insert = db.prepare(`
      INSERT OR IGNORE INTO session_agents (session_id, agent_id, enabled, permissions, joined_at)
      VALUES (?, ?, 1, ?, ?)
    `);
    const tx = db.transaction(() => {
      for (const agent of agents) insert.run(sessionId, agent.id, JSON.stringify(DEFAULT_MEMBER_PERMISSIONS), now);
    });
    tx();
  } catch (e) {
    console.warn("[Group] 初始化群成员失败(忽略):", e?.message || e);
  }
}

// 按指定 AI 成员创建群；为空则回退到全员
function initSessionMembers(sessionId, agentIds) {
  try {
    const wanted = Array.isArray(agentIds) ? [...new Set(agentIds.map(String))] : [];
    const allowed = new Set(listAgents(false).map((a) => a.id));
    const list = wanted.filter((id) => allowed.has(id));
    if (list.length === 0) { ensureSessionMembers(sessionId); return; }
    const now = new Date().toISOString();
    const insert = db.prepare(`
      INSERT OR IGNORE INTO session_agents (session_id, agent_id, enabled, permissions, joined_at)
      VALUES (?, ?, 1, ?, ?)
    `);
    const tx = db.transaction(() => {
      for (const id of list) insert.run(sessionId, id, JSON.stringify(DEFAULT_MEMBER_PERMISSIONS), now);
    });
    tx();
  } catch (e) {
    console.warn("[Group] 初始化群成员失败(忽略):", e?.message || e);
    ensureSessionMembers(sessionId);
  }
}

function getSessionMemberRecords(sessionId) {
  ensureSessionMembers(sessionId);
  const rows = db.prepare(`
    SELECT sa.agent_id, sa.enabled, sa.permissions, sa.joined_at, sa.sort_order,
           a.name, a.role, a.avatar, a.color
    FROM session_agents sa
    JOIN agents a ON a.id = sa.agent_id
    WHERE sa.session_id = ?
    ORDER BY sa.enabled DESC, sa.sort_order ASC, sa.joined_at ASC, a.name ASC
  `).all(sessionId);
  return rows.map((r) => ({
    agentId: r.agent_id,
    name: r.name,
    role: r.role,
    avatar: r.avatar,
    color: r.color,
    enabled: Boolean(r.enabled),
    permissions: parseMemberPermissions(r.permissions),
    joinedAt: r.joined_at,
    sortOrder: Number(r.sort_order || 0)
  }));
}

function getActiveSessionMembers(sessionId) {
  const records = getSessionMemberRecords(sessionId)
    .filter((m) => m.enabled && m.permissions.speak !== false);
  const byId = new Map(listAgents(true).map((a) => [a.id, a]));
  const members = records.map((m) => ({ ...byId.get(m.agentId), permissions: m.permissions })).filter((a) => a.id);
  return members.length > 0 ? members : listAgents(true).map((a) => ({ ...a, permissions: { ...DEFAULT_MEMBER_PERMISSIONS } }));
}

function addSessionMember(sessionId, agentId) {
  const session = getSession(sessionId);
  if (!session) return { error: "会话不存在" };
  const agent = listAgents(false).find((a) => a.id === agentId);
  if (!agent) return { error: "AI 成员不存在" };
  const count = db.prepare("SELECT COUNT(*) AS c FROM session_agents WHERE session_id = ?").get(sessionId).c;
  const limit = Number(session.member_limit) || 20;
  if (count >= limit) return { error: `群人数已达上限（${limit}），请先扩容` };
  db.prepare(`
    INSERT OR IGNORE INTO session_agents (session_id, agent_id, enabled, permissions, joined_at)
    VALUES (?, ?, 1, ?, ?)
  `).run(sessionId, agentId, JSON.stringify(DEFAULT_MEMBER_PERMISSIONS), new Date().toISOString());
  return { success: true };
}

function updateSessionMember(sessionId, agentId, patch = {}) {
  const row = db.prepare("SELECT * FROM session_agents WHERE session_id = ? AND agent_id = ?").get(sessionId, agentId);
  if (!row) return { error: "该成员不在群内" };
  const perms = parseMemberPermissions(row.permissions);
  if (patch.permissions && typeof patch.permissions === "object") {
    for (const key of Object.keys(DEFAULT_MEMBER_PERMISSIONS)) {
      if (patch.permissions[key] !== void 0) perms[key] = Boolean(patch.permissions[key]);
    }
  }
  const enabled = patch.enabled !== void 0 ? (patch.enabled ? 1 : 0) : row.enabled;
  const sortOrder = patch.sortOrder !== void 0
    ? Math.max(0, Math.min(999, Number(patch.sortOrder) || 0))
    : row.sort_order;
  db.prepare("UPDATE session_agents SET enabled = ?, permissions = ?, sort_order = ? WHERE session_id = ? AND agent_id = ?")
    .run(enabled, JSON.stringify(perms), sortOrder, sessionId, agentId);
  return { success: true, enabled: Boolean(enabled), permissions: perms, sortOrder: Number(sortOrder || 0) };
}

function removeSessionMember(sessionId, agentId) {
  const r = db.prepare("DELETE FROM session_agents WHERE session_id = ? AND agent_id = ?").run(sessionId, agentId);
  return { success: r.changes > 0 };
}

app.get("/api/sessions", (req, res) => {
  try {
    const sessions = db.prepare(`
      SELECT s.*,
        (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS messageCount,
        (SELECT COUNT(*) FROM session_agents sa WHERE sa.session_id = s.id AND sa.enabled = 1) AS memberCount,
        (SELECT content FROM messages m WHERE m.session_id = s.id ORDER BY m.created_at DESC LIMIT 1) AS lastMessage,
        (SELECT created_at FROM messages m WHERE m.session_id = s.id ORDER BY m.created_at DESC LIMIT 1) AS lastMessageAt,
        (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id AND m.role != 'user'
          AND m.created_at > COALESCE(NULLIF(s.last_read_at, ''), s.created_at)) AS unreadCount
      FROM sessions s ORDER BY s.pinned DESC, s.updated_at DESC
    `).all();
    res.json({ sessions });
  } catch (error) {
    res.status(500).json({ error: error?.message || "\u83B7\u53D6\u4F1A\u8BDD\u5931\u8D25" });
  }
});
app.get("/api/sessions/:sessionId", (req, res) => {
  try {
    const { sessionId } = req.params;
    const session = getSession(sessionId);
    if (!session) return res.status(404).json({ error: "\u4F1A\u8BDD\u4E0D\u5B58\u5728" });
    const messages = getMessagesBySession(sessionId).map((msg) => ({
      ...msg,
      tool_calls: msg.tool_calls ? JSON.parse(msg.tool_calls) : null
    }));
    res.json({ session, messages });
  } catch (error) {
    res.status(500).json({ error: error?.message || "\u83B7\u53D6\u4F1A\u8BDD\u5931\u8D25" });
  }
});
app.post("/api/sessions", (req, res) => {
  try {
    const body = req.body || {};
    const model = body.model || getDefaultModel();
    const title = body.title || "新对话";
    const now = (/* @__PURE__ */ new Date()).toISOString();
    const session = createSession({
      id: uuidv44(),
      title,
      model,
      sdk_session_id: null,
      avatar: body.avatar,
      category: body.category,
      tags: body.tags,
      intro: body.intro,
      location: body.location,
      announcement: body.announcement,
      member_limit: body.memberLimit ?? body.member_limit,
      join_policy: body.joinPolicy ?? body.join_policy,
      created_at: now,
      updated_at: now
    });
    initSessionMembers(session.id, body.memberIds);
    res.json({ session: getSession(session.id) });
  } catch (error) {
    res.status(500).json({ error: error?.message || "\u521B\u5EFA\u4F1A\u8BDD\u5931\u8D25" });
  }
});
app.patch("/api/sessions/:sessionId", (req, res) => {
  try {
    const session = getSession(req.params.sessionId);
    if (!session) return res.status(404).json({ error: "会话不存在" });
    const body = req.body || {};
    const fieldMap = {
      title: (v) => String(v).slice(0, 200),
      model: (v) => String(v),
      avatar: (v) => String(v || "👥").slice(0, 8),
      category: (v) => String(v || "家庭").slice(0, 40),
      tags: (v) => JSON.stringify(Array.isArray(v) ? v.slice(0, 20).map(String) : []),
      intro: (v) => String(v || "").slice(0, 2000),
      location: (v) => String(v || "").slice(0, 200),
      announcement: (v) => String(v || "").slice(0, 2000),
      my_nickname: (v) => String(v || "").slice(0, 80),
      remark: (v) => String(v || "").slice(0, 200),
      pinned: (v) => (v ? 1 : 0),
      muted: (v) => (v ? 1 : 0),
      folded: (v) => (v ? 1 : 0),
      saved: (v) => (v ? 1 : 0),
      member_limit: (v) => Math.min(Math.max(Number(v) || 20, 1), 500),
      join_policy: (v) => (["manual", "auto", "confirm"].includes(v) ? v : "manual"),
      speech_mode: (v) => (["quiet", "standard", "active", "mention"].includes(v) ? v : "standard")
    };
    const aliases = {
      myNickname: "my_nickname",
      memberLimit: "member_limit",
      joinPolicy: "join_policy",
      speechMode: "speech_mode"
    };
    const updates = {};
    for (const [rawKey, value] of Object.entries(body)) {
      const key = aliases[rawKey] || rawKey;
      if (!fieldMap[key]) continue;
      updates[key] = fieldMap[key](value);
    }
    if (updates.member_limit !== void 0) {
      const memberCount = db.prepare("SELECT COUNT(*) AS c FROM session_agents WHERE session_id = ?").get(session.id).c;
      updates.member_limit = Math.max(updates.member_limit, memberCount || 1);
    }
    const keys = Object.keys(updates);
    if (keys.length === 0) return res.json({ success: true, session });
    const sql = `UPDATE sessions SET ${keys.map((k) => `${k} = ?`).join(", ")}, updated_at = ? WHERE id = ?`;
    db.prepare(sql).run(...keys.map((k) => updates[k]), new Date().toISOString(), session.id);
    res.json({ success: true, session: getSession(session.id) });
  } catch (error) {
    res.status(500).json({ error: error?.message || "\u66F4\u65B0\u4F1A\u8BDD\u5931\u8D25" });
  }
});
app.get("/api/sessions/:sessionId/members", (req, res) => {
  try {
    const session = getSession(req.params.sessionId);
    if (!session) return res.status(404).json({ error: "会话不存在" });
    const members = getSessionMemberRecords(session.id);
    res.json({
      success: true,
      members,
      memberCount: members.filter((m) => m.enabled).length,
      memberLimit: Number(session.member_limit) || 20,
      joinPolicy: session.join_policy || "manual"
    });
  } catch (e) {
    res.status(500).json({ error: e?.message || "读取群成员失败" });
  }
});
app.post("/api/sessions/:sessionId/members", (req, res) => {
  try {
    const agentId = String(req.body?.agentId || "").trim();
    if (!agentId) return res.status(400).json({ error: "请选择 AI 成员" });
    const result = addSessionMember(req.params.sessionId, agentId);
    if (result.error) return res.status(400).json({ error: result.error });
    res.json({ success: true, members: getSessionMemberRecords(req.params.sessionId) });
  } catch (e) {
    res.status(500).json({ error: e?.message || "添加成员失败" });
  }
});
app.patch("/api/sessions/:sessionId/members/:agentId", (req, res) => {
  try {
    const result = updateSessionMember(req.params.sessionId, req.params.agentId, req.body || {});
    if (result.error) return res.status(404).json({ error: result.error });
    res.json({ success: true, ...result });
  } catch (e) {
    res.status(500).json({ error: e?.message || "更新成员失败" });
  }
});
app.delete("/api/sessions/:sessionId/members/:agentId", (req, res) => {
  try {
    const result = removeSessionMember(req.params.sessionId, req.params.agentId);
    if (!result.success) return res.status(404).json({ error: "该成员不在群内" });
    res.json({ success: true });
  } catch (e) {
    res.status(500).json({ error: e?.message || "移除成员失败" });
  }
});
app.post("/api/sessions/:sessionId/members/expand", (req, res) => {
  try {
    const session = getSession(req.params.sessionId);
    if (!session) return res.status(404).json({ error: "会话不存在" });
    const current = Number(session.member_limit) || 20;
    const target = Math.min(Math.max(Number(req.body?.limit) || 50, current), 500);
    db.prepare("UPDATE sessions SET member_limit = ?, updated_at = ? WHERE id = ?")
      .run(target, new Date().toISOString(), session.id);
    res.json({ success: true, memberLimit: target });
  } catch (e) {
    res.status(500).json({ error: e?.message || "扩容失败" });
  }
});
app.delete("/api/sessions/:sessionId", (req, res) => {
  try {
    const success = deleteSession(req.params.sessionId);
    if (!success) return res.status(404).json({ error: "\u4F1A\u8BDD\u4E0D\u5B58\u5728" });
    try { db.prepare("DELETE FROM session_agents WHERE session_id = ?").run(req.params.sessionId); } catch { /* 忽略 */ }
    try { db.prepare("DELETE FROM group_files WHERE session_id = ?").run(req.params.sessionId); } catch { /* 忽略 */ }
    res.json({ success: true });
  } catch (error) {
    res.status(500).json({ error: error?.message || "\u5220\u9664\u4F1A\u8BDD\u5931\u8D25" });
  }
});

// 标记群已读
app.post("/api/sessions/:sessionId/read", (req, res) => {
  try {
    const r = db.prepare("UPDATE sessions SET last_read_at = ? WHERE id = ?")
      .run(new Date().toISOString(), req.params.sessionId);
    res.json({ success: r.changes > 0 });
  } catch (e) {
    res.status(500).json({ error: e?.message || "标记已读失败" });
  }
});

// 消息搜索
// v2.9：消息撤回（本地单人产品：允许撤回本群任意一条消息，撤回后内容清空）
app.post("/api/messages/:messageId/recall", (req, res) => {
  try {
    const id = String(req.params.messageId || "");
    const msg = db.prepare("SELECT id, session_id FROM messages WHERE id = ?").get(id);
    if (!msg) return res.status(404).json({ error: "消息不存在" });
    const now = new Date().toISOString();
    db.prepare("UPDATE messages SET content = '', recalled = 1, recalled_at = ? WHERE id = ?").run(now, id);
    db.prepare("UPDATE sessions SET updated_at = ? WHERE id = ?").run(now, msg.session_id);
    res.json({ success: true, messageId: id, recalled: true });
  } catch (e) {
    res.status(500).json({ error: e?.message || "撤回失败" });
  }
});
app.get("/api/messages/search", (req, res) => {
  try {
    const q = String(req.query.q || "").trim();
    if (!q) return res.json({ success: true, results: [], count: 0 });
    const sessionId = req.query.sessionId ? String(req.query.sessionId) : null;
    const limit = Math.min(Math.max(Number(req.query.limit) || 30, 1), 100);
    const rows = sessionId
      ? db.prepare(`
          SELECT m.id, m.session_id, m.role, m.content, m.agent_name, m.agent_color, m.created_at, s.title AS session_title
          FROM messages m JOIN sessions s ON s.id = m.session_id
          WHERE m.content LIKE ? AND m.session_id = ? AND m.recalled = 0
          ORDER BY m.created_at DESC LIMIT ?
        `).all(`%${q}%`, sessionId, limit)
      : db.prepare(`
          SELECT m.id, m.session_id, m.role, m.content, m.agent_name, m.agent_color, m.created_at, s.title AS session_title
          FROM messages m JOIN sessions s ON s.id = m.session_id
          WHERE m.content LIKE ? AND m.recalled = 0
          ORDER BY m.created_at DESC LIMIT ?
        `).all(`%${q}%`, limit);
    res.json({
      success: true,
      count: rows.length,
      results: rows.map((r) => ({
        id: r.id,
        sessionId: r.session_id,
        sessionTitle: r.session_title,
        role: r.role,
        agentName: r.agent_name || "",
        agentColor: r.agent_color || "",
        content: String(r.content || "").slice(0, 800),
        createdAt: r.created_at
      }))
    });
  } catch (e) {
    res.status(500).json({ error: e?.message || "搜索消息失败" });
  }
});

// v2.9 撤回消息（群聊深化）：仅允许撤回自己发送的消息
app.delete("/api/messages/:id", (req, res) => {
  try {
    const id = String(req.params.id);
    const msg = db.prepare("SELECT id, session_id, role FROM messages WHERE id = ?").get(id);
    if (!msg) return res.status(404).json({ error: "消息不存在或已撤回" });
    if (msg.role !== "user") return res.status(403).json({ error: "只能撤回自己发送的消息" });
    db.prepare("DELETE FROM messages WHERE id = ?").run(id);
    // 清理引用该消息的回复，避免出现悬空引用
    try { db.prepare("UPDATE messages SET reply_to_id = NULL WHERE reply_to_id = ?").run(id); } catch { /* 忽略：字段可能未迁移 */ }
    res.json({ success: true, id, sessionId: msg.session_id });
  } catch (e) {
    res.status(500).json({ error: e?.message || "撤回失败" });
  }
});

// ==================== 群文件 ====================
function getGroupFilesDir(sessionId) {
  return path2.join(getWorkspaceRoot(), "group-files", String(sessionId));
}

function mapGroupFile(row, sessionId) {
  return {
    id: row.id,
    sessionId: row.session_id,
    name: row.name,
    size: row.size,
    mime: row.mime || "",
    uploadedBy: row.uploaded_by || "human",
    createdAt: row.created_at,
    url: `/api/sessions/${encodeURIComponent(sessionId)}/files/${encodeURIComponent(row.id)}`
  };
}

app.get("/api/sessions/:sessionId/files", (req, res) => {
  try {
    const session = getSession(req.params.sessionId);
    if (!session) return res.status(404).json({ error: "会话不存在" });
    const rows = db.prepare("SELECT * FROM group_files WHERE session_id = ? ORDER BY created_at DESC").all(session.id);
    res.json({ success: true, files: rows.map((r) => mapGroupFile(r, session.id)) });
  } catch (e) {
    res.status(500).json({ error: e?.message || "读取群文件失败" });
  }
});

app.post("/api/sessions/:sessionId/files", (req, res) => {
  try {
    const session = getSession(req.params.sessionId);
    if (!session) return res.status(404).json({ error: "会话不存在" });
    const rawName = String(req.body?.name || "file").trim();
    const name = path2.basename(rawName).replace(/[<>:"/\\|?*\x00-\x1f]/g, "_").slice(0, 120) || "file";
    let base64 = String(req.body?.dataBase64 || "");
    const comma = base64.indexOf(",");
    if (base64.startsWith("data:") && comma !== -1) base64 = base64.slice(comma + 1);
    if (!base64) return res.status(400).json({ error: "文件内容为空" });
    const buffer = Buffer.from(base64, "base64");
    if (buffer.length === 0) return res.status(400).json({ error: "文件内容为空" });
    if (buffer.length > 10 * 1024 * 1024) return res.status(400).json({ error: "单个群文件最多 10MB" });
    const id = `gf-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const dir = getGroupFilesDir(session.id);
    fs2.mkdirSync(dir, { recursive: true });
    const storedName = `${id}-${name}`;
    const abs = path2.join(dir, storedName);
    fs2.writeFileSync(abs, buffer);
    const storedPath = path2.relative(getWorkspaceRoot(), abs).split(path2.sep).join("/");
    const createdAt = new Date().toISOString();
    db.prepare(`
      INSERT INTO group_files (id, session_id, name, stored_path, size, mime, uploaded_by, created_at)
      VALUES (?, ?, ?, ?, ?, ?, 'human', ?)
    `).run(id, session.id, name, storedPath, buffer.length, String(req.body?.mime || "application/octet-stream"), createdAt);
    const row = db.prepare("SELECT * FROM group_files WHERE id = ?").get(id);
    res.json({ success: true, file: mapGroupFile(row, session.id) });
  } catch (e) {
    res.status(500).json({ error: e?.message || "上传群文件失败" });
  }
});

app.get("/api/sessions/:sessionId/files/:fileId", (req, res) => {
  try {
    const row = db.prepare("SELECT * FROM group_files WHERE id = ? AND session_id = ?")
      .get(req.params.fileId, req.params.sessionId);
    if (!row) return res.status(404).json({ error: "群文件不存在" });
    const root = getWorkspaceRoot();
    const abs = path2.resolve(root, row.stored_path);
    const rel = path2.relative(root, abs);
    if (!rel || rel.startsWith("..") || path2.isAbsolute(rel) || !fs2.existsSync(abs)) {
      return res.status(404).json({ error: "文件已丢失" });
    }
    res.download(abs, row.name);
  } catch (e) {
    res.status(500).json({ error: e?.message || "下载群文件失败" });
  }
});

app.delete("/api/sessions/:sessionId/files/:fileId", (req, res) => {
  try {
    const row = db.prepare("SELECT * FROM group_files WHERE id = ? AND session_id = ?")
      .get(req.params.fileId, req.params.sessionId);
    if (!row) return res.status(404).json({ error: "群文件不存在" });
    try {
      const root = getWorkspaceRoot();
      const abs = path2.resolve(root, row.stored_path);
      const rel = path2.relative(root, abs);
      if (rel && !rel.startsWith("..") && !path2.isAbsolute(rel) && fs2.existsSync(abs)) fs2.unlinkSync(abs);
    } catch { /* 忽略 */ }
    db.prepare("DELETE FROM group_files WHERE id = ?").run(row.id);
    res.json({ success: true });
  } catch (e) {
    res.status(500).json({ error: e?.message || "删除群文件失败" });
  }
});
app.get("/api/agents", (req, res) => {
  res.json({ agents: listAgents(false) });
});
app.post("/api/agents", (req, res) => {
  try {
    const { name, role, avatar, color, systemPrompt, keywords, intents, enabled, sort } = req.body;
    if (!name) return res.status(400).json({ error: "\u6210\u5458\u540D\u79F0\u4E0D\u80FD\u4E3A\u7A7A" });
    const agent = addAgent({ name, role, avatar, color, systemPrompt, keywords, intents, enabled, sort });
    // 按群的加入策略自动接纳新 AI 成员：auto 直接加入，confirm 先待确认
    try {
      const sessions = db.prepare("SELECT id, join_policy FROM sessions WHERE join_policy IN ('auto', 'confirm')").all();
      const now = new Date().toISOString();
      const insert = db.prepare(`
        INSERT OR IGNORE INTO session_agents (session_id, agent_id, enabled, permissions, joined_at)
        VALUES (?, ?, ?, ?, ?)
      `);
      for (const s of sessions) {
        insert.run(s.id, agent.id, s.join_policy === "auto" ? 1 : 0, JSON.stringify(DEFAULT_MEMBER_PERMISSIONS), now);
      }
    } catch (e) {
      console.warn("[Group] 新成员自动入群失败(忽略):", e?.message || e);
    }
    res.json({ agent });
  } catch (error) {
    res.status(500).json({ error: error?.message || "\u521B\u5EFA\u6210\u5458\u5931\u8D25" });
  }
});
app.patch("/api/agents/:id", (req, res) => {
  const success = modifyAgent(req.params.id, req.body);
  if (!success) return res.status(404).json({ error: "\u6210\u5458\u4E0D\u5B58\u5728" });
  res.json({ success: true });
});
app.delete("/api/agents/:id", (req, res) => {
  const success = removeAgent(req.params.id);
  if (!success) return res.status(404).json({ error: "\u6210\u5458\u4E0D\u5B58\u5728" });
  res.json({ success: true });
});
app.get("/api/faq", (req, res) => {
  res.json({ faq: listFaq() });
});
app.get("/api/faq/search", (req, res) => {
  const q = String(req.query.q || "");
  res.json({ query: q, hits: searchFaq(q, 5) });
});
app.post("/api/faq", (req, res) => {
  try {
    const { question, answer, category, keywords, enabled } = req.body;
    if (!question || !answer) return res.status(400).json({ error: "\u95EE\u9898\u4E0E\u7B54\u6848\u4E0D\u80FD\u4E3A\u7A7A" });
    const item = addFaq({ question, answer, category, keywords, enabled });
    rebuildFaqFts();
    res.json({ faq: item });
  } catch (error) {
    res.status(500).json({ error: error?.message || "\u65B0\u589E\u5931\u8D25" });
  }
});
app.patch("/api/faq/:id", (req, res) => {
  const success = modifyFaq(req.params.id, req.body);
  if (!success) return res.status(404).json({ error: "\u6761\u76EE\u4E0D\u5B58\u5728" });
  rebuildFaqFts();
  res.json({ success: true });
});
app.delete("/api/faq/:id", (req, res) => {
  const success = removeFaq(req.params.id);
  if (!success) return res.status(404).json({ error: "\u6761\u76EE\u4E0D\u5B58\u5728" });
  rebuildFaqFts();
  res.json({ success: true });
});
app.get("/api/faq/stats", (req, res) => {
  try {
    const all = getAllFaq();
    const byCategory = {};
    let totalHits = 0;
    let enabled = 0;
    for (const f of all) {
      byCategory[f.category || "通用"] = (byCategory[f.category || "通用"] || 0) + 1;
      totalHits += Number(f.hits) || 0;
      if (f.enabled === 1) enabled++;
    }
    res.json({
      success: true,
      stats: {
        total: all.length,
        enabled,
        disabled: all.length - enabled,
        totalHits,
        byCategory,
        fts: faqFtsAvailable
      }
    });
  } catch (e) {
    res.status(500).json({ error: e?.message || "读取失败" });
  }
});

app.get("/api/faq/export", (req, res) => {
  try {
    res.json({
      version: 1,
      exportedAt: new Date().toISOString(),
      items: listFaq()
    });
  } catch (e) {
    res.status(500).json({ error: e?.message || "导出失败" });
  }
});

app.post("/api/faq/import", (req, res) => {
  try {
    const raw = Array.isArray(req.body) ? req.body : (Array.isArray(req.body?.items) ? req.body.items : null);
    if (!raw) return res.status(400).json({ error: "导入内容需包含 items 数组" });
    let added = 0;
    let updated = 0;
    let skipped = 0;
    for (const item of raw.slice(0, 500)) {
      const question = String(item?.question || "").trim();
      const answer = String(item?.answer || "").trim();
      if (!question || !answer) { skipped++; continue; }
      const existing = db.prepare("SELECT id FROM faq WHERE question = ?").get(question);
      if (existing) {
        db.prepare("UPDATE faq SET answer = ?, category = ?, keywords = ?, enabled = ?, updated_at = ? WHERE id = ?")
          .run(answer, String(item.category || "通用"), JSON.stringify(item.keywords || []),
            item.enabled === false ? 0 : 1, new Date().toISOString(), existing.id);
        updated++;
      } else {
        addFaq({
          question, answer,
          category: item.category || "通用",
          keywords: Array.isArray(item.keywords) ? item.keywords : [],
          enabled: item.enabled !== false
        });
        added++;
      }
    }
    rebuildFaqFts();
    res.json({ success: true, added, updated, skipped });
  } catch (e) {
    res.status(500).json({ error: e?.message || "导入失败" });
  }
});

// 手动重建 FTS5 索引（索引异常时使用）
app.post("/api/faq/rebuild-fts", (req, res) => {
  try {
    if (!faqFtsAvailable) {
      return res.json({ success: false, fts: false, message: "FTS5 不可用，当前走朴素匹配" });
    }
    rebuildFaqFts();
    res.json({ success: true, fts: true, message: "FTS5 索引已重建" });
  } catch (e) {
    res.status(500).json({ error: e?.message || "重建失败" });
  }
});

app.get("/api/intent/defs", (req, res) => {
  res.json({
    intents: INTENT_DEFS.map((d) => ({
      id: d.id,
      name: d.name,
      description: d.description,
      keywords: d.keywords,
      agents: d.agents
    }))
  });
});
app.post("/api/intent/recognize", async (req, res) => {
  try {
    const { text, mode = "auto", model } = req.body;
    if (!text) return res.status(400).json({ error: "\u6587\u672C\u4E0D\u80FD\u4E3A\u7A7A" });
    const result = await recognizeIntent(text, {
      mode,
      model: model || getDefaultModel(),
      agents: listAgents(true).map((a) => ({ id: a.id, name: a.name, role: a.role }))
    });
    res.json({ result });
  } catch (error) {
    res.status(500).json({ error: error?.message || "\u8BC6\u522B\u5931\u8D25" });
  }
});
app.post("/api/chat", async (req, res) => {
  const {
    sessionId,
    message,
    model,
    intentMode = "auto",
    maxAgents = 3,
    enableSummary = true,
    permissionMode = "default",
    cwd,
    mode = "group",
    targetAgentId = null,
    workflowType = "default",
    images = null,
    allowWrites = false,
    resumeTaskId = null,
    replyToId = null
  } = req.body;
  if (!message) return res.status(400).json({ error: "\u6D88\u606F\u4E0D\u80FD\u4E3A\u7A7A" });
  let session = sessionId ? getSession(sessionId) : null;
  const now = (/* @__PURE__ */ new Date()).toISOString();
  if (!session) {
    session = createSession({
      id: sessionId || uuidv44(),
      title: message.slice(0, 30) + (message.length > 30 ? "..." : ""),
      model: model || getDefaultModel(),
      sdk_session_id: null,
      created_at: now,
      updated_at: now
    });
  }
  const selectedModel = model
    || (Array.isArray(images) && images.length > 0 ? getModelRoute("vision") : "")
    || session.model
    || getDefaultModel();
  // 更新会话模式
  if (mode && mode !== session.mode) {
    updateSession(session.id, { mode, target_agent_id: targetAgentId });
  }

  // 设置 SSE（包含心跳）
  const { cleanup } = setupSSE(res);

  try {
    // 根据模式选择处理函数
    if (mode === "private" && targetAgentId) {
      // 1v1 私聊模式
      await runPrivateChat(res, {
        sessionId: session.id,
        message,
        model: selectedModel,
        targetAgentId,
        permissionMode,
        cwd,
        images,
        allowWrites
      });
    } else if (mode === "workflow") {
      // 工作流模式
      await runWorkflow(res, {
        sessionId: session.id,
        message,
        model: selectedModel,
        workflowType,
        permissionMode,
        cwd
      });
    } else if (mode === "proactive") {
      // 主动服务模式
      await runProactiveService(res, {
        sessionId: session.id,
        message,
        model: selectedModel,
        permissionMode,
        cwd,
        images,
        allowWrites
      });
    } else if (mode === "continuous") {
      // 持续对话模式
      await runContinuousDialogue(res, {
        sessionId: session.id,
        message,
        model: selectedModel,
        permissionMode,
        cwd,
        images,
        allowWrites
      });
    } else if (mode === "creative") {
      // 创作协作模式
      await runCreativeCollaboration(res, {
        sessionId: session.id,
        message,
        model: selectedModel,
        permissionMode,
        cwd,
        images,
        allowWrites
      });
    } else if (mode === "analysis") {
      // 数据分析模式
      await runDataAnalysis(res, {
        sessionId: session.id,
        message,
        model: selectedModel,
        permissionMode,
        cwd,
        images,
        allowWrites
      });
    } else if (mode === "autonomous") {
      // 自主代理模式
      await runAutonomousAgent(res, {
        sessionId: session.id,
        message,
        model: selectedModel,
        permissionMode,
        cwd,
        images,
        allowWrites,
        resumeTaskId
      });
    } else if (mode === "cross_platform") {
      // 跨平台模式
      await runCrossPlatform(res, {
        sessionId: session.id,
        message,
        model: selectedModel,
        permissionMode,
        cwd,
        images,
        allowWrites
      });
    } else {
      // 默认群聊模式
      await runGroupChat(res, {
        sessionId: session.id,
        message,
        model: selectedModel,
        intentMode,
        maxAgents: clampMaxAgents(maxAgents),
        enableSummary: enableSummary !== false,
        permissionMode,
        cwd,
        images,
        allowWrites,
        replyToId
      });
    }
    const messages = getMessagesBySession(session.id);
    if (messages.length <= 2) {
      updateSession(session.id, {
        title: message.slice(0, 30) + (message.length > 30 ? "..." : ""),
        model: selectedModel
      });
    }
    res.end();
  } catch (error) {
    console.error("[Chat] 处理失败:", error);
    sendSSE(res, { type: "error", message: error?.message || "处理失败" });
    res.end();
  } finally {
    cleanup();
  }
});

// ==================== 工作流模板 CRUD ====================
app.get("/api/workflow-templates", (req, res) => {
  try {
    const rows = db.prepare("SELECT * FROM workflow_templates ORDER BY created_at DESC").all();
    rows.forEach((r) => {
      try { r.stages = JSON.parse(r.stages); } catch { r.stages = []; }
    });
    res.json({ success: true, templates: rows });
  } catch (error) {
    res.status(500).json({ error: error?.message || "获取失败" });
  }
});
app.post("/api/workflow-templates", (req, res) => {
  try {
    const { name, description = "", stages = [], category = "" } = req.body || {};
    if (!name || !Array.isArray(stages) || stages.length === 0) {
      return res.status(400).json({ error: "模板名称与至少一个阶段必填" });
    }
    const now = new Date().toISOString();
    const id = `wf-${uuidv44().slice(0, 8)}`;
    db.prepare(
      "INSERT INTO workflow_templates (id, name, description, stages, category, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)"
    ).run(id, name, description, JSON.stringify(stages), category, now, now);
    res.json({ success: true, id });
  } catch (error) {
    res.status(500).json({ error: error?.message || "创建失败" });
  }
});
app.patch("/api/workflow-templates/:id", (req, res) => {
  try {
    const { name, description, stages, category, enabled } = req.body || {};
    const fields = [];
    const values = [];
    if (name !== undefined) { fields.push("name = ?"); values.push(name); }
    if (description !== undefined) { fields.push("description = ?"); values.push(description); }
    if (stages !== undefined) {
      if (!Array.isArray(stages) || stages.length === 0) return res.status(400).json({ error: "阶段不能为空数组" });
      fields.push("stages = ?"); values.push(JSON.stringify(stages));
    }
    if (category !== undefined) { fields.push("category = ?"); values.push(category); }
    if (enabled !== undefined) { fields.push("enabled = ?"); values.push(enabled ? 1 : 0); }
    if (fields.length === 0) return res.status(400).json({ error: "无更新字段" });
    fields.push("updated_at = ?");
    values.push(new Date().toISOString(), req.params.id);
    const r = db.prepare(`UPDATE workflow_templates SET ${fields.join(", ")} WHERE id = ?`).run(...values);
    res.json({ success: r.changes > 0 });
  } catch (error) {
    res.status(500).json({ error: error?.message || "更新失败" });
  }
});
app.delete("/api/workflow-templates/:id", (req, res) => {
  try {
    const r = db.prepare("DELETE FROM workflow_templates WHERE id = ?").run(req.params.id);
    res.json({ success: r.changes > 0 });
  } catch (error) {
    res.status(500).json({ error: error?.message || "删除失败" });
  }
});

// ==================== 提示词配置 / 智力档位 / 审计日志 ====================
app.get("/api/prompts", (req, res) => {
  try {
    const scope = String(req.query.scope || "global");
    res.json({ success: true, config: promptStore.get(scope) });
  } catch (error) {
    res.status(500).json({ error: error?.message || "读取失败" });
  }
});
app.post("/api/prompts", (req, res) => {
  try {
    const { scope = "global", system_prompt, step_prompt, style_prompt, enforce } = req.body || {};
    const ok = promptStore.save(scope, { system_prompt, step_prompt, style_prompt, enforce });
    if (!ok) return res.status(400).json({ error: "无更新字段" });
    res.json({ success: true, config: promptStore.get(scope) });
  } catch (error) {
    res.status(500).json({ error: error?.message || "保存失败" });
  }
});

app.get("/api/intelligence", (req, res) => {
  res.json({ success: true, level: appIntelligenceLevel, levels: INTELLIGENCE_LEVELS });
});
app.post("/api/intelligence", (req, res) => {
  try {
    const level = Number(req.body?.level);
    if (!INTELLIGENCE_LEVELS.some((l) => l.level === level)) {
      return res.status(400).json({ error: "档位必须是 1-6" });
    }
    appIntelligenceLevel = level;
    process.env.AI_INTELLIGENCE_LEVEL = String(level);
    // 持久化到 .env
    try {
      const envPath = path2.join(__dirname2, "..", ".env");
      let lines = fs2.existsSync(envPath) ? fs2.readFileSync(envPath, "utf-8").split(/\r?\n/) : [];
      const idx = lines.findIndex((l) => l.trim().startsWith("AI_INTELLIGENCE_LEVEL="));
      if (idx >= 0) lines[idx] = `AI_INTELLIGENCE_LEVEL=${level}`;
      else lines.push(`AI_INTELLIGENCE_LEVEL=${level}`);
      fs2.writeFileSync(envPath, lines.filter((l) => l.trim()).join("\n") + "\n", "utf-8");
    } catch (e) {
      console.warn("[Intelligence] .env 写入失败(仅内存生效):", e?.message || e);
    }
    res.json({ success: true, level, detail: getIntelligenceLevel(level) });
  } catch (error) {
    res.status(500).json({ error: error?.message || "设置失败" });
  }
});

app.get("/api/audit-logs", (req, res) => {
  try {
    const { sessionId, status, limit = 200, offset = 0 } = req.query;
    const logs = auditLogger.query({
      sessionId: sessionId ? String(sessionId) : undefined,
      status: status ? String(status) : undefined,
      limit: Number(limit), offset: Number(offset)
    });
    res.json({ success: true, logs });
  } catch (error) {
    res.status(500).json({ error: error?.message || "查询失败" });
  }
});
app.get("/api/audit-summary", (req, res) => {
  try {
    const sessionId = req.query.sessionId ? String(req.query.sessionId) : null;
    res.json({ success: true, summary: auditLogger.summary(sessionId) });
  } catch (error) {
    res.status(500).json({ error: error?.message || "统计失败" });
  }
});

// Token 用量：明细 + 汇总
app.get("/api/token-usage", (req, res) => {
  try {
    const { sessionId, limit = 100 } = req.query;
    const where = sessionId ? "WHERE session_id = ?" : "";
    const params = sessionId ? [String(sessionId)] : [];
    const rows = db.prepare(
      `SELECT * FROM token_usage ${where} ORDER BY created_at DESC LIMIT ?`
    ).all(...params, Number(limit) || 100);
    const sum = db.prepare(
      `SELECT COALESCE(SUM(prompt_tokens),0) AS prompt, COALESCE(SUM(completion_tokens),0) AS completion,
              COALESCE(SUM(total_tokens),0) AS total, COUNT(*) AS calls,
              COALESCE(MAX(context_chars),0) AS max_ctx_chars, COALESCE(MAX(context_messages),0) AS max_ctx_msgs
       FROM token_usage ${where}`
    ).get(...params);
    res.json({ success: true, rows, summary: sum });
  } catch (error) {
    res.status(500).json({ error: error?.message || "查询失败" });
  }
});

// ==================== 设置中心 / 用量中心 ====================
app.get("/api/settings", (req, res) => {
  try {
    const countByKind = (kind) => {
      try {
        return db.prepare("SELECT COUNT(*) AS c FROM store_items WHERE kind = ? AND enabled = 1").get(kind)?.c || 0;
      } catch { return 0; }
    };
    res.json({
      success: true,
      tools: {
        fileTools: envFlag("FILE_TOOLS_ENABLED"),
        terminal: envFlag("TERMINAL_ENABLED"),
        gui: envFlag("GUI_ENABLED"),
        screenCapture: envFlag("SCREEN_CAPTURE_ENABLED"),
        sandbox: envFlag("SANDBOX_MODE", "0")
      },
      preferences: {
        intelligenceLevel: appIntelligenceLevel,
        maxAgents: Number(process.env.MAX_AGENTS) || 3,
        defaultMode: process.env.DEFAULT_CHAT_MODE || "group",
        defaultCwd: process.env.DEFAULT_WORK_DIR || "workspace"
      },
      pricing: getUsagePricing(),
      modelRoutes: getModelRoutes(),
      backup: {
        auto: autoBackupEnabled(),
        dir: BACKUP_ROOT
      },
      counts: {
        skills: countByKind("skill"),
        plugins: countByKind("plugin"),
        mcp: countByKind("mcp")
      },
      family: {
        enabled: Boolean(getFamilyPassword()),
        bindHost: BIND_HOST,
        lanAccess: !isLoopbackHost(BIND_HOST)
      },
      version: {
        app: "AI 神殿",
        version: "2.9",
        build: "2026-10-19",
        node: process.version
      }
    });
  } catch (e) {
    res.status(500).json({ error: e?.message || "读取设置失败" });
  }
});

app.post("/api/settings/tools", (req, res) => {
  try {
    const body = req.body || {};
    const map = {
      fileTools: "FILE_TOOLS_ENABLED",
      terminal: "TERMINAL_ENABLED",
      gui: "GUI_ENABLED",
      screenCapture: "SCREEN_CAPTURE_ENABLED",
      sandbox: "SANDBOX_MODE"
    };
    const vars = {};
    for (const [key, envKey] of Object.entries(map)) {
      if (body[key] !== undefined) vars[envKey] = body[key] ? "1" : "0";
    }
    if (Object.keys(vars).length === 0) return res.status(400).json({ error: "没有可保存的开关" });
    writeEnvVars(vars);
    res.json({
      success: true,
      tools: {
        fileTools: envFlag("FILE_TOOLS_ENABLED"),
        terminal: envFlag("TERMINAL_ENABLED"),
        gui: envFlag("GUI_ENABLED"),
        screenCapture: envFlag("SCREEN_CAPTURE_ENABLED"),
        sandbox: envFlag("SANDBOX_MODE", "0")
      }
    });
  } catch (e) {
    res.status(500).json({ error: e?.message || "保存工具设置失败" });
  }
});

app.post("/api/settings/preferences", (req, res) => {
  try {
    const { intelligenceLevel, maxAgents, defaultMode, defaultCwd } = req.body || {};
    const vars = {};
    if (intelligenceLevel !== undefined) {
      const level = Number(intelligenceLevel);
      if (!INTELLIGENCE_LEVELS.some((l) => l.level === level)) {
        return res.status(400).json({ error: "智力档位必须是 1-6" });
      }
      appIntelligenceLevel = level;
      vars.AI_INTELLIGENCE_LEVEL = String(level);
    }
    if (maxAgents !== undefined) {
      const n = Math.max(1, Math.min(8, Number(maxAgents) || 3));
      vars.MAX_AGENTS = String(n);
    }
    if (defaultMode !== undefined) {
      const allowed = ["group", "private", "workflow", "proactive", "continuous", "creative", "analysis", "autonomous", "cross_platform"];
      if (!allowed.includes(String(defaultMode))) return res.status(400).json({ error: "默认模式不合法" });
      vars.DEFAULT_CHAT_MODE = String(defaultMode);
    }
    if (defaultCwd !== undefined) {
      vars.DEFAULT_WORK_DIR = String(defaultCwd).slice(0, 260) || "workspace";
    }
    if (Object.keys(vars).length === 0) return res.status(400).json({ error: "没有可保存的偏好" });
    writeEnvVars(vars);
    res.json({
      success: true,
      preferences: {
        intelligenceLevel: appIntelligenceLevel,
        maxAgents: Number(process.env.MAX_AGENTS) || 3,
        defaultMode: process.env.DEFAULT_CHAT_MODE || "group",
        defaultCwd: process.env.DEFAULT_WORK_DIR || "workspace"
      }
    });
  } catch (e) {
    res.status(500).json({ error: e?.message || "保存偏好失败" });
  }
});

app.post("/api/settings/pricing", (req, res) => {
  try {
    const { input, output, cached, currency } = req.body || {};
    const clean = (v) => {
      const n = Number(v);
      return Number.isFinite(n) && n >= 0 ? n : 0;
    };
    const vars = {
      USAGE_INPUT_PRICE: String(clean(input)),
      USAGE_OUTPUT_PRICE: String(clean(output)),
      USAGE_CACHED_PRICE: String(clean(cached)),
      USAGE_CURRENCY: String(currency || "CNY").toUpperCase() === "USD" ? "USD" : "CNY"
    };
    writeEnvVars(vars);
    res.json({ success: true, pricing: getUsagePricing() });
  } catch (e) {
    res.status(500).json({ error: e?.message || "保存计费设置失败" });
  }
});

app.post("/api/settings/model-routes", (req, res) => {
  try {
    const routes = saveModelRoutes(req.body || {});
    res.json({ success: true, modelRoutes: routes });
  } catch (e) {
    res.status(500).json({ error: e?.message || "保存模型分工失败" });
  }
});

app.post("/api/settings/family", (req, res) => {
  try {
    const enabled = Boolean(req.body?.enabled);
    const input = String(req.body?.password || "").trim();
    const current = getFamilyPassword();
    if (enabled && !input && !current) {
      return res.status(400).json({ error: "启用家庭访问前请设置口令" });
    }
    if (enabled && input && input.length < 4) {
      return res.status(400).json({ error: "家庭访问口令至少 4 位" });
    }
    const next = enabled ? (input || current) : "";
    writeEnvVars({ FAMILY_ACCESS_PASSWORD: next });
    if (!next) {
      res.setHeader("Set-Cookie", "family_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0");
    }
    res.json({
      success: true,
      family: {
        enabled: Boolean(next),
        bindHost: BIND_HOST,
        lanAccess: !isLoopbackHost(BIND_HOST)
      },
      requireLogin: Boolean(next) && next !== current
    });
  } catch (e) {
    res.status(500).json({ error: e?.message || "保存家庭访问设置失败" });
  }
});

// ==================== 站内通知（v2.4） ====================
function createNotification({ type = "system", title, body = "", sessionId = null, link = null }) {
  try {
    const id = `notif-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    db.prepare(`
      INSERT INTO notifications (id, type, title, body, session_id, link, read, created_at)
      VALUES (?, ?, ?, ?, ?, ?, 0, ?)
    `).run(id, type, String(title || "").slice(0, 200), String(body || "").slice(0, 2000), sessionId || null, link || null, new Date().toISOString());
    return id;
  } catch (e) {
    console.warn("[Notify] 写入通知失败(忽略):", e?.message || e);
    return null;
  }
}

function mapNotification(row) {
  return {
    id: row.id,
    type: row.type,
    title: row.title,
    body: row.body || "",
    sessionId: row.session_id || null,
    link: row.link || null,
    read: Boolean(row.read),
    createdAt: row.created_at
  };
}

app.get("/api/notifications", (req, res) => {
  try {
    const limit = Math.min(Math.max(Number(req.query.limit) || 50, 1), 100);
    const unreadOnly = req.query.unread === "1";
    const rows = unreadOnly
      ? db.prepare("SELECT * FROM notifications WHERE read = 0 ORDER BY created_at DESC LIMIT ?").all(limit)
      : db.prepare("SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?").all(limit);
    const unread = db.prepare("SELECT COUNT(*) AS c FROM notifications WHERE read = 0").get().c;
    res.json({ success: true, notifications: rows.map(mapNotification), unreadCount: unread });
  } catch (e) {
    res.status(500).json({ error: e?.message || "读取通知失败" });
  }
});

// 桌面壳轮询：仅本机可访问，返回指定时间之后的新通知
app.get("/api/desktop/notifications", (req, res) => {
  if (!isLoopbackRequest(req)) return res.status(403).json({ error: "仅允许本机访问" });
  try {
    const after = String(req.query.after || "1970-01-01T00:00:00.000Z");
    const rows = db.prepare("SELECT * FROM notifications WHERE created_at > ? ORDER BY created_at ASC LIMIT 20").all(after);
    const unread = db.prepare("SELECT COUNT(*) AS c FROM notifications WHERE read = 0").get().c;
    res.json({ success: true, notifications: rows.map(mapNotification), unreadCount: unread });
  } catch (e) {
    res.status(500).json({ error: e?.message || "读取桌面通知失败" });
  }
});

// 桌面壳全局划词：启动器把捕获到的选中文本暂存，工作台轮询后取走
let pendingDesktopSelection = null;

app.post("/api/desktop/selection", (req, res) => {
  if (!isLoopbackRequest(req)) return res.status(403).json({ error: "仅允许本机访问" });
  try {
    const text = String(req.body?.text || "").trim();
    if (!text) return res.status(400).json({ error: "选中内容为空" });
    if (text.length > 12000) return res.status(400).json({ error: "选中内容过长（最多 12000 字符）" });
    pendingDesktopSelection = {
      id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
      text,
      createdAt: new Date().toISOString()
    };
    res.json({ success: true, id: pendingDesktopSelection.id });
  } catch (e) {
    res.status(500).json({ error: e?.message || "保存划词内容失败" });
  }
});

app.get("/api/desktop/selection", (req, res) => {
  if (!isLoopbackRequest(req)) return res.status(403).json({ error: "仅允许本机访问" });
  const after = String(req.query.after || "");
  const item = pendingDesktopSelection && pendingDesktopSelection.id !== after ? pendingDesktopSelection : null;
  res.json({ success: true, selection: item });
});

app.post("/api/desktop/selection/ack", (req, res) => {
  if (!isLoopbackRequest(req)) return res.status(403).json({ error: "仅允许本机访问" });
  const id = String(req.body?.id || "");
  if (pendingDesktopSelection && pendingDesktopSelection.id === id) pendingDesktopSelection = null;
  res.json({ success: true });
});

app.post("/api/notifications/read-all", (req, res) => {
  try {
    const r = db.prepare("UPDATE notifications SET read = 1 WHERE read = 0").run();
    res.json({ success: true, updated: r.changes });
  } catch (e) {
    res.status(500).json({ error: e?.message || "标记已读失败" });
  }
});

app.post("/api/notifications/read-session", (req, res) => {
  try {
    const sessionId = String(req.body?.sessionId || "").trim();
    if (!sessionId) return res.status(400).json({ error: "缺少 sessionId" });
    const r = db.prepare("UPDATE notifications SET read = 1 WHERE session_id = ? AND read = 0").run(sessionId);
    res.json({ success: true, updated: r.changes });
  } catch (e) {
    res.status(500).json({ error: e?.message || "标记已读失败" });
  }
});

app.post("/api/notifications/:id/read", (req, res) => {
  try {
    const r = db.prepare("UPDATE notifications SET read = 1 WHERE id = ?").run(req.params.id);
    res.json({ success: r.changes > 0 });
  } catch (e) {
    res.status(500).json({ error: e?.message || "标记已读失败" });
  }
});

// ==================== 数据备份与恢复（v2.4） ====================
const BACKUP_ROOT = path2.join(dataDir, "backups");

function getAppSetting(key, fallback = "") {
  try {
    const row = db.prepare("SELECT value FROM app_settings WHERE key = ?").get(key);
    return row?.value === void 0 || row?.value === null ? fallback : String(row.value);
  } catch {
    return fallback;
  }
}

function setAppSetting(key, value) {
  const now = new Date().toISOString();
  db.prepare(`
    INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)
    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
  `).run(key, String(value), now);
}

function autoBackupEnabled() {
  return getAppSetting("auto_backup", "1") !== "0";
}

function ensureBackupRoot() {
  fs2.mkdirSync(BACKUP_ROOT, { recursive: true });
}

function getBackupDir(id) {
  const raw = String(id || "").trim();
  if (!raw) return null;
  const abs = path2.resolve(BACKUP_ROOT, raw);
  const rel = path2.relative(BACKUP_ROOT, abs);
  if (!rel || rel.startsWith("..") || path2.isAbsolute(rel)) return null;
  return abs;
}

function dirSize(dir) {
  let total = 0;
  try {
    for (const entry of fs2.readdirSync(dir, { withFileTypes: true })) {
      const abs = path2.join(dir, entry.name);
      if (entry.isDirectory()) total += dirSize(abs);
      else if (entry.isFile()) total += fs2.statSync(abs).size;
    }
  } catch { /* 忽略 */ }
  return total;
}

// 递归复制目录（不用 fs.cpSync：当前运行时 cpSync 会导致进程崩溃）
function copyDirSafe(src, dest, depth, budget) {
  if (depth > 20 || budget.bytes > budget.maxBytes) return;
  let stats;
  try { stats = fs2.lstatSync(src); } catch { return; }
  if (stats.isSymbolicLink()) return;
  if (!stats.isDirectory()) return;
  fs2.mkdirSync(dest, { recursive: true });
  for (const entry of fs2.readdirSync(src, { withFileTypes: true })) {
    if (budget.bytes > budget.maxBytes) break;
    if (entry.isSymbolicLink()) continue;
    const s = path2.join(src, entry.name);
    const d = path2.join(dest, entry.name);
    if (entry.isDirectory()) {
      copyDirSafe(s, d, depth + 1, budget);
    } else if (entry.isFile()) {
      try {
        const size = fs2.statSync(s).size;
        if (budget.bytes + size > budget.maxBytes) break;
        fs2.copyFileSync(s, d);
        budget.bytes += size;
      } catch { /* 跳过无法复制的文件 */ }
    }
  }
}

function listBackups() {
  ensureBackupRoot();
  const items = [];
  for (const entry of fs2.readdirSync(BACKUP_ROOT, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const dir = path2.join(BACKUP_ROOT, entry.name);
    let manifest = {};
    try {
      manifest = JSON.parse(fs2.readFileSync(path2.join(dir, "manifest.json"), "utf8"));
    } catch { /* 忽略 */ }
    const dbFile = path2.join(dir, "chat.db");
    items.push({
      id: entry.name,
      createdAt: manifest.createdAt || fs2.statSync(dir).mtime.toISOString(),
      auto: Boolean(manifest.auto),
      dbBytes: manifest.dbBytes ?? (fs2.existsSync(dbFile) ? fs2.statSync(dbFile).size : 0),
      workspace: Boolean(manifest.workspace),
      workspaceBytes: Number(manifest.workspaceBytes) || 0,
      totalBytes: dirSize(dir)
    });
  }
  return items.sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)));
}

function pruneBackups(keep = 10) {
  try {
    const items = listBackups();
    for (const item of items.slice(keep)) {
      const dir = getBackupDir(item.id);
      if (dir && fs2.existsSync(dir)) fs2.rmSync(dir, { recursive: true, force: true });
    }
  } catch (e) {
    console.warn("[Backup] 清理旧备份失败(忽略):", e?.message || e);
  }
}

async function createBackup({ includeWorkspace = true, auto = false } = {}) {
  ensureBackupRoot();
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  const id = `backup-${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
  const dir = path2.join(BACKUP_ROOT, id);
  fs2.mkdirSync(dir, { recursive: true });
  const dbFile = path2.join(dir, "chat.db");
  await db.backup(dbFile);
  let workspace = false;
  let workspaceBytes = 0;
  if (includeWorkspace) {
    try {
      const wsRoot = getWorkspaceRoot();
      if (wsRoot && fs2.existsSync(wsRoot)) {
        const size = dirSize(wsRoot);
        if (size <= 300 * 1024 * 1024) {
          const budget = { maxBytes: 300 * 1024 * 1024, bytes: 0 };
          copyDirSafe(wsRoot, path2.join(dir, "workspace"), 0, budget);
          workspace = true;
          workspaceBytes = budget.bytes;
        }
      }
    } catch (e) {
      console.warn("[Backup] 工作区复制失败(忽略):", e?.message || e);
    }
  }
  const manifest = {
    id,
    createdAt: now.toISOString(),
    auto: Boolean(auto),
    dbBytes: fs2.statSync(dbFile).size,
    workspace,
    workspaceBytes,
    totalBytes: dirSize(dir),
    version: "2.9"
  };
  fs2.writeFileSync(path2.join(dir, "manifest.json"), JSON.stringify(manifest, null, 2));
  pruneBackups(10);
  return manifest;
}

// 恢复：先做安全备份，再用 ATTACH 把备份表数据写回当前库
async function restoreBackup(id) {
  const dir = getBackupDir(id);
  if (!dir) throw new Error("备份不存在");
  const dbFile = path2.join(dir, "chat.db");
  if (!fs2.existsSync(dbFile)) throw new Error("备份中缺少 chat.db");

  const safetyId = `backup-before-restore-${Date.now()}`;
  const safetyDir = path2.join(BACKUP_ROOT, safetyId);
  fs2.mkdirSync(safetyDir, { recursive: true });
  await db.backup(path2.join(safetyDir, "chat.db"));
  fs2.writeFileSync(path2.join(safetyDir, "manifest.json"), JSON.stringify({
    id: safetyId, createdAt: new Date().toISOString(), auto: true, note: "恢复前自动安全备份"
  }, null, 2));

  const bak = new Database(dbFile, { readonly: true });
  try {
    const liveTables = db.prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'").all().map((t) => t.name);
    const bakTables = new Set(bak.prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'").all().map((t) => t.name));
    try { db.prepare("DETACH DATABASE bak").run(); } catch { /* 忽略 */ }
    db.prepare("ATTACH DATABASE ? AS bak").run(dbFile);
    db.pragma("foreign_keys = OFF");
    const copy = db.transaction(() => {
      for (const table of liveTables) {
        if (table.startsWith("faq_fts")) continue;
        if (!bakTables.has(table)) continue;
        const liveCols = db.prepare(`PRAGMA main.table_info(${table})`).all().map((c) => c.name);
        const bakCols = new Set(db.prepare(`PRAGMA bak.table_info(${table})`).all().map((c) => c.name));
        const cols = liveCols.filter((c) => bakCols.has(c));
        if (cols.length === 0) continue;
        const colSql = cols.map((c) => `"${c}"`).join(",");
        db.prepare(`DELETE FROM main.${table}`).run();
        db.prepare(`INSERT INTO main.${table} (${colSql}) SELECT ${colSql} FROM bak.${table}`).run();
      }
    });
    copy();
    db.pragma("foreign_keys = ON");
    try { db.prepare("INSERT INTO faq_fts(faq_fts) VALUES('rebuild')").run(); } catch { /* 忽略 */ }
    try { db.prepare("DETACH DATABASE bak").run(); } catch { /* 忽略 */ }
  } finally {
    bak.close();
  }
  // 工作区文件：先备份当前工作区，再把备份里的文件覆盖回来（保留多出来的文件）
  let workspaceRestored = false;
  try {
    const wsBackup = path2.join(dir, "workspace");
    if (fs2.existsSync(wsBackup)) {
      const wsRoot = getWorkspaceRoot();
      if (wsRoot) {
        const budget = { maxBytes: 300 * 1024 * 1024, bytes: 0 };
        copyDirSafe(wsRoot, path2.join(safetyDir, "workspace"), 0, { ...budget, bytes: 0 });
        copyDirSafe(wsBackup, wsRoot, 0, budget);
        workspaceRestored = true;
      }
    }
  } catch (e) {
    console.warn("[Backup] 工作区恢复失败(忽略):", e?.message || e);
  }
  return { restored: id, safetyBackup: safetyId, workspaceRestored };
}

async function runAutoBackupIfDue() {
  if (!autoBackupEnabled()) return null;
  const latest = listBackups()[0];
  if (latest && Date.now() - new Date(latest.createdAt).getTime() < 24 * 3600e3) return null;
  return createBackup({ includeWorkspace: true, auto: true });
}

app.get("/api/backups", (req, res) => {
  try {
    res.json({ success: true, backups: listBackups(), auto: autoBackupEnabled() });
  } catch (e) {
    res.status(500).json({ error: e?.message || "读取备份失败" });
  }
});

app.post("/api/backups", async (req, res) => {
  try {
    const backup = await createBackup({ includeWorkspace: req.body?.includeWorkspace !== false, auto: false });
    res.json({ success: true, backup });
  } catch (e) {
    res.status(500).json({ error: e?.message || "创建备份失败" });
  }
});

app.delete("/api/backups/:id", (req, res) => {
  try {
    const dir = getBackupDir(req.params.id);
    if (!dir || !fs2.existsSync(dir)) return res.status(404).json({ error: "备份不存在" });
    fs2.rmSync(dir, { recursive: true, force: true });
    res.json({ success: true });
  } catch (e) {
    res.status(500).json({ error: e?.message || "删除备份失败" });
  }
});

app.post("/api/backups/:id/restore", async (req, res) => {
  try {
    if (req.body?.confirm !== true) return res.status(400).json({ error: "恢复操作需要确认" });
    const result = await restoreBackup(req.params.id);
    res.json({ success: true, ...result });
  } catch (e) {
    res.status(500).json({ error: e?.message || "恢复备份失败" });
  }
});

app.post("/api/settings/backup", (req, res) => {
  try {
    setAppSetting("auto_backup", req.body?.auto === false ? "0" : "1");
    res.json({ success: true, auto: autoBackupEnabled() });
  } catch (e) {
    res.status(500).json({ error: e?.message || "保存备份设置失败" });
  }
});

// ==================== 开放 API + Webhook（v2.9 多 Key 权限分级） ====================
const OPEN_API_PERMISSIONS = {
  read: ['/api/open/health', '/api/open/chat', '/api/open/tasks', '/api/open/tasks/:id'],
  write: ['/api/open/tasks', '/api/open/chat'],  // 创建任务、发起对话
  admin: ['*']  // 所有开放接口
};

function getOpenApiKeys() {
  try {
    const val = getAppSetting("open_api_keys", "[]");
    return JSON.parse(val);
  } catch {
    return [];
  }
}

function setOpenApiKeys(keys) {
  setAppSetting("open_api_keys", JSON.stringify(keys));
}

function getOpenApiConfig() {
  return {
    enabled: getAppSetting("open_api_enabled", "0") === "1",
    webhookUrl: getAppSetting("webhook_url", ""),
    webhookEnabled: getAppSetting("webhook_enabled", "0") === "1",
    keys: getOpenApiKeys()
  };
}

function ensureOpenApiKey() {
  const cfg = getOpenApiConfig();
  if (cfg.keys.length > 0) return cfg.keys[0].key;
  const key = crypto.randomBytes(24).toString("hex");
  const keys = [{ id: 'default', name: '默认 Key', key, permissions: ['admin'], createdAt: new Date().toISOString(), lastUsed: null }];
  setOpenApiKeys(keys);
  return key;
}

function findApiKey(provided) {
  const keys = getOpenApiKeys();
  for (const k of keys) {
    if (k.key.length === provided.length && crypto.timingSafeEqual(Buffer.from(k.key), Buffer.from(provided))) {
      return k;
    }
  }
  return null;
}

function hasPermission(keyObj, req) {
  if (!keyObj.permissions) return false;
  if (keyObj.permissions.includes('admin')) return true;
  
  const path = req.path;
  const method = req.method;
  
  // 写权限：POST /api/open/tasks（创建任务）、POST /api/open/chat（发起对话）
  if (keyObj.permissions.includes('write') && method === 'POST' && 
      (path === '/api/open/tasks' || path === '/api/open/chat')) return true;
  
  // 读权限：GET /api/open/health、GET /api/open/tasks、GET /api/open/tasks/:id、GET /api/open/chat（不存在GET）
  if (keyObj.permissions.includes('read') && method === 'GET' && 
      (path === '/api/open/health' || path.startsWith('/api/open/tasks'))) return true;
  
  return false;
}

function requireOpenApiKey(req, res, next) {
  const cfg = getOpenApiConfig();
  if (!cfg.enabled) return res.status(403).json({ error: "开放 API 未启用" });
  const provided = String(req.headers["x-api-key"] || "").trim();
  if (!provided) return res.status(401).json({ error: "缺少 X-API-Key" });
  const keyObj = findApiKey(provided);
  if (!keyObj) return res.status(401).json({ error: "API Key 无效" });
  if (!hasPermission(keyObj, req)) return res.status(403).json({ error: "权限不足", required: keyObj.permissions });
  req.openApiKey = keyObj;
  // 更新最后使用时间
  const keys = getOpenApiKeys();
  const idx = keys.findIndex(k => k.id === keyObj.id);
  if (idx >= 0) {
    keys[idx].lastUsed = new Date().toISOString();
    setOpenApiKeys(keys);
  }
  next();
}

async function sendWebhook(event, payload = {}) {
  const cfg = getOpenApiConfig();
  if (!cfg.webhookEnabled || !cfg.webhookUrl || !/^https?:\/\//i.test(cfg.webhookUrl)) return false;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 8000);
  try {
    await fetch(cfg.webhookUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-AI-Temple-Event": event },
      body: JSON.stringify({ event, timestamp: new Date().toISOString(), ...payload }),
      signal: controller.signal
    });
    return true;
  } catch (e) {
    console.warn("[Webhook] 推送失败(忽略):", e?.message || e);
    return false;
  } finally {
    clearTimeout(timer);
  }
}

app.get("/api/open/config", (req, res) => {
  try {
    const cfg = getOpenApiConfig();
    const keys = cfg.keys.map(k => ({
      ...k,
      key: k.key ? `${k.key.slice(0, 6)}…${k.key.slice(-6)}` : '',
      keyFull: k.key // 仅管理界面返回完整 key
    }));
    res.json({
      success: true,
      enabled: cfg.enabled,
      keys,
      webhookUrl: cfg.webhookUrl,
      webhookEnabled: cfg.webhookEnabled,
      endpoints: {
        chat: "/api/open/chat",
        tasks: "/api/open/tasks",
        taskDetail: "/api/open/tasks/:id",
        health: "/api/open/health"
      }
    });
  } catch (e) {
    res.status(500).json({ error: e?.message || "读取开放接口配置失败" });
  }
});

app.post("/api/open/config", (req, res) => {
  try {
    const body = req.body || {};
    if (body.enabled !== void 0) setAppSetting("open_api_enabled", body.enabled ? "1" : "0");
    if (body.webhookEnabled !== void 0) setAppSetting("webhook_enabled", body.webhookEnabled ? "1" : "0");
    if (body.webhookUrl !== void 0) setAppSetting("webhook_url", String(body.webhookUrl || "").slice(0, 1000));
    
    const keys = getOpenApiKeys();
    let key;
    
    // 处理多 Key 操作
    if (body.keys) {
      // 批量更新 keys
      setOpenApiKeys(body.keys);
    } else if (body.addKey) {
      // 添加新 Key
      const newKey = {
        id: `key-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        name: body.addKey.name || `Key ${keys.length + 1}`,
        key: crypto.randomBytes(24).toString("hex"),
        permissions: body.addKey.permissions || ['read'],
        createdAt: new Date().toISOString(),
        lastUsed: null
      };
      keys.push(newKey);
      setOpenApiKeys(keys);
    } else if (body.removeKey) {
      // 删除 Key
      const idx = keys.findIndex(k => k.id === body.removeKey);
      if (idx >= 0) keys.splice(idx, 1);
      setOpenApiKeys(keys);
    } else if (body.updateKey) {
      // 更新 Key
      const idx = keys.findIndex(k => k.id === body.updateKey.id);
      if (idx >= 0) {
        keys[idx] = { ...keys[idx], ...body.updateKey };
        setOpenApiKeys(keys);
      }
    } else if (body.regenerateKey || keys.length === 0) {
      // 兼容旧版：重新生成默认 Key
      key = ensureOpenApiKey();
    } else if (body.enabled) {
      ensureOpenApiKey();
    }
    
    const cfg = getOpenApiConfig();
    res.json({ success: true, enabled: cfg.enabled, keys: cfg.keys, webhookUrl: cfg.webhookUrl, webhookEnabled: cfg.webhookEnabled });
  } catch (e) {
    res.status(500).json({ error: e?.message || "保存开放接口配置失败" });
  }
});

app.post("/api/open/webhook/test", async (req, res) => {
  try {
    const ok = await sendWebhook("webhook.test", { message: "AI 神殿 Webhook 测试" });
    res.json({ success: ok, message: ok ? "已发送测试请求" : "未启用或推送失败" });
  } catch (e) {
    res.status(500).json({ error: e?.message || "测试失败" });
  }
});

app.get("/api/open/health", requireOpenApiKey, (req, res) => {
  res.json({ success: true, app: "AI 神殿", version: "2.9", time: new Date().toISOString() });
});

app.post("/api/open/chat", requireOpenApiKey, async (req, res) => {
  try {
    const message = String(req.body?.message || "").trim();
    if (!message) return res.status(400).json({ error: "message 不能为空" });
    const sessionId = req.body?.sessionId ? String(req.body.sessionId) : null;
    const session = sessionId ? getSession(sessionId) : null;
    if (sessionId && !session) return res.status(404).json({ error: "会话不存在" });
    const cfg = getActiveProviderConfig();
    if (!cfg.baseUrl || !cfg.apiKey) {
      return res.status(400).json({ error: `提供商「${cfg.key}」未配置 Base URL 或 API Key` });
    }
    const model = String(req.body?.model || getDefaultModel() || cfg.model);
    const history = session
      ? getMessagesBySession(session.id).slice(-10).map((m) => ({ role: m.role, content: m.content }))
      : [];
    const now = new Date().toISOString();
    if (session) {
      createMessage({
        id: uuidv44(), session_id: session.id, role: "user", content: message,
        model: null, created_at: now
      });
    }
    const result = await runAgentTurnLoop({
      provider: cfg.protocol === "claude" ? "claude" : "openai",
      providerConfig: { baseUrl: cfg.baseUrl, apiKey: cfg.apiKey },
      model,
      systemPrompt: "你是 AI 神殿开放接口助手。请用简洁中文回答外部系统的问题，只输出回答正文。",
      userPrompt: message,
      history,
      images: [],
      context: {},
      maxTurns: 1,
      temperature: 0.4,
      disableTools: true,
      onText: () => {},
      onToolStart: () => {},
      onToolResult: () => {}
    });
    const reply = String(result.content || "").trim();
    if (session) {
      createMessage({
        id: uuidv44(), session_id: session.id, role: "assistant", content: reply,
        model, created_at: new Date().toISOString(),
        agent_name: "开放接口", agent_color: "#22d3ee", reply_source: "open_api"
      });
      db.prepare("UPDATE sessions SET updated_at = ? WHERE id = ?").run(new Date().toISOString(), session.id);
    }
    recordTokenUsage({
      sessionId: session?.id || null, agentId: null, mode: "open_api",
      model, usage: result.usage
    });
    res.json({ success: true, reply, sessionId: session?.id || null, model, usage: result.usage || null });
  } catch (e) {
    res.status(500).json({ error: e?.message || "开放接口调用失败" });
  }
});

app.post("/api/open/tasks", requireOpenApiKey, (req, res) => {
  try {
    const body = req.body || {};
    const goal = String(body.goal || "").trim();
    const sessionId = String(body.sessionId || "").trim();
    if (!goal) return res.status(400).json({ error: "goal 不能为空" });
    if (!sessionId) return res.status(400).json({ error: "sessionId 不能为空" });
    if (!getSession(sessionId)) return res.status(404).json({ error: "会话不存在" });
    const task = taskQueue.enqueue({
      sessionId, goal,
      priority: Number(body.priority) || 0,
      dueAt: body.dueAt || null,
      allowWrites: body.allowWrites ? 1 : 0,
      origin: "open_api"
    });
    res.json({ success: true, task: mapAutonomousTask(task) });
  } catch (e) {
    res.status(500).json({ error: e?.message || "创建任务失败" });
  }
});

app.get("/api/open/tasks/:id", requireOpenApiKey, (req, res) => {
  try {
    const row = db.prepare("SELECT * FROM autonomous_tasks WHERE id = ?").get(req.params.id);
    if (!row) return res.status(404).json({ error: "任务不存在" });
    res.json({ success: true, task: mapAutonomousTask(row) });
  } catch (e) {
    res.status(500).json({ error: e?.message || "读取任务失败" });
  }
});

// 用量总览：Token / 成本估算 / 缓存命中 / 每日热力图 / 模型分布
app.get("/api/usage/overview", (req, res) => {
  try {
    const days = Math.max(1, Math.min(365, Number(req.query.days) || 30));
    const now = new Date();
    const start = new Date(now.getFullYear(), now.getMonth(), now.getDate() - (days - 1));
    const fmt = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    const since = fmt(start);
    const rows = db.prepare(`
      SELECT date(created_at, 'localtime') AS day, model,
             COUNT(*) AS requests,
             COALESCE(SUM(prompt_tokens), 0) AS prompt,
             COALESCE(SUM(completion_tokens), 0) AS completion,
             COALESCE(SUM(total_tokens), 0) AS total,
             COALESCE(SUM(cached_tokens), 0) AS cached,
             COALESCE(SUM(estimated), 0) AS estimated
      FROM token_usage
      WHERE date(created_at, 'localtime') >= ?
      GROUP BY day, model
    `).all(since);

    const pricing = getUsagePricing();
    const costOf = (p, c, cached) =>
      ((Math.max(0, p - cached) * pricing.input) + (cached * pricing.cached) + (c * pricing.output)) / 1e6;

    const dailyMap = new Map();
    const modelMap = new Map();
    for (const row of rows) {
      const cost = costOf(row.prompt, row.completion, row.cached);
      const day = dailyMap.get(row.day) || { date: row.day, requests: 0, prompt: 0, completion: 0, total: 0, cached: 0, cost: 0, estimated: 0 };
      day.requests += row.requests;
      day.prompt += row.prompt;
      day.completion += row.completion;
      day.total += row.total;
      day.cached += row.cached;
      day.cost += cost;
      day.estimated += row.estimated;
      dailyMap.set(row.day, day);

      const modelName = row.model || "未知模型";
      const model = modelMap.get(modelName) || { model: modelName, requests: 0, prompt: 0, completion: 0, total: 0, cached: 0, cost: 0, estimated: 0 };
      model.requests += row.requests;
      model.prompt += row.prompt;
      model.completion += row.completion;
      model.total += row.total;
      model.cached += row.cached;
      model.cost += cost;
      model.estimated += row.estimated;
      modelMap.set(modelName, model);
    }

    const daily = [];
    for (let i = 0; i < days; i++) {
      const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
      const key = fmt(d);
      const item = dailyMap.get(key) || { date: key, requests: 0, prompt: 0, completion: 0, total: 0, cached: 0, cost: 0, estimated: 0 };
      item.cost = Number(item.cost.toFixed(6));
      daily.push(item);
    }

    const totals = daily.reduce((acc, d) => {
      acc.requests += d.requests;
      acc.prompt += d.prompt;
      acc.completion += d.completion;
      acc.total += d.total;
      acc.cached += d.cached;
      acc.cost += d.cost;
      acc.estimatedCalls += d.estimated;
      return acc;
    }, { requests: 0, prompt: 0, completion: 0, total: 0, cached: 0, cost: 0, estimatedCalls: 0 });
    totals.cost = Number(totals.cost.toFixed(6));

    const models = [...modelMap.values()]
      .map((m) => ({ ...m, cost: Number(m.cost.toFixed(6)) }))
      .sort((a, b) => b.total - a.total);
    const activeDays = daily.filter((d) => d.requests > 0).length;
    const peakDay = daily.reduce((best, d) => (d.total > best.total ? d : best), daily[0] || { date: since, total: 0 });
    const topModel = models[0] || null;
    const cacheHitRate = totals.prompt > 0 ? totals.cached / totals.prompt : 0;
    const dailyAverage = days > 0 ? totals.total / days : 0;

    res.json({
      success: true,
      range: { days, from: since, to: fmt(now) },
      pricing,
      totals,
      cacheHitRate,
      activeDays,
      peakDay,
      topModel,
      dailyAverage,
      daily,
      models
    });
  } catch (e) {
    res.status(500).json({ error: e?.message || "读取用量失败" });
  }
});

// ==================== 工具页：更新日志 / 翻译 / 截图 ====================
app.get("/api/changelog", (req, res) => {
  try {
    const file = path2.join(__dirname2, "..", "更新日志.md");
    if (!fs2.existsSync(file)) return res.json({ success: true, versions: [] });
    const lines = fs2.readFileSync(file, "utf-8").split(/\r?\n/);
    const versions = [];
    let current = null;
    let section = null;
    for (const raw of lines) {
      const line = raw.trim();
      const heading = line.match(/^##\s+(v[\d.]+)\s*(?:·\s*)?(.*)$/);
      if (heading) {
        current = { version: heading[1], title: heading[2] || "", date: "", sections: [] };
        versions.push(current);
        section = null;
        continue;
      }
      if (!current) continue;
      const dateMatch = line.match(/^\*\*日期[：:]\*\*\s*(.*)$/);
      if (dateMatch) current.date = dateMatch[1].trim();
      const sectionMatch = line.match(/^\*\*(新增|修复|验证|提示)\*\*/);
      if (sectionMatch) {
        section = { name: sectionMatch[1], items: [] };
        current.sections.push(section);
        continue;
      }
      if (section && line.startsWith("- ")) section.items.push(line.slice(2).trim());
    }
    res.json({ success: true, versions: versions.filter((v) => v.version) });
  } catch (e) {
    res.status(500).json({ error: e?.message || "读取更新日志失败" });
  }
});

app.post("/api/tools/translate", async (req, res) => {
  try {
    const { text, targetLang = "简体中文", sourceLang = "自动检测", model = null } = req.body || {};
    const input = String(text || "").trim();
    if (!input) return res.status(400).json({ error: "翻译内容不能为空" });
    if (input.length > 20000) return res.status(400).json({ error: "单次翻译最多 20000 字符" });
    const cfg = getActiveProviderConfig();
    if (!cfg.baseUrl || !cfg.apiKey) {
      return res.status(400).json({ error: `提供商「${cfg.key}」未配置 Base API Key` });
    }
    const translateModel = model || getModelRoute("translation") || cfg.model;
    const result = await runAgentTurnLoop({
      provider: cfg.protocol === "claude" ? "claude" : "openai",
      providerConfig: { baseUrl: cfg.baseUrl, apiKey: cfg.apiKey },
      model: translateModel,
      systemPrompt:
        `你是专业翻译。请把用户提供的文本翻译为「${targetLang}」。\n` +
        `要求：忠实原意、术语准确、保留段落与格式、只输出译文，不要解释、不要加引号。`,
      userPrompt: `源语言：${sourceLang}\n目标语言：${targetLang}\n\n${input}`,
      history: [],
      images: [],
      context: {},
      maxTurns: 1,
      temperature: 0.2,
      disableTools: true,
      onText: () => {},
      onToolStart: () => {},
      onToolResult: () => {}
    });
    const translation = String(result.content || "").trim();
    recordTokenUsage({
      sessionId: null, agentId: null, mode: "translate",
      model: translateModel, usage: result.usage
    });
    res.json({ success: true, translation, targetLang, sourceLang, usage: result.usage || null });
  } catch (e) {
    res.status(500).json({ error: e?.message || "翻译失败" });
  }
});

app.post("/api/tools/text-action", async (req, res) => {
  try {
    const action = String(req.body?.action || "").trim();
    const input = String(req.body?.text || "").trim();
    if (!input) return res.status(400).json({ error: "选中内容不能为空" });
    if (input.length > 12000) return res.status(400).json({ error: "单次最多 12000 字符" });
    const presets = {
      explain: {
        title: "解释",
        system: "你是讲解助手。只解释用户给出的文本，输出简洁中文，不要解释任务本身，不要客套。",
        user: (text) => `请解释下面这段文本：\n"""\n${text}\n"""`
      },
      summarize: {
        title: "总结",
        system: "你是总结助手。只总结用户给出的文本，输出简洁中文，不要解释任务本身，不要客套。",
        user: (text) => `请总结下面这段文本：\n"""\n${text}\n"""`
      }
    };
    const preset = presets[action];
    if (!preset) return res.status(400).json({ error: "未知操作" });
    const cfg = getActiveProviderConfig();
    if (!cfg.baseUrl || !cfg.apiKey) {
      return res.status(400).json({ error: `提供商「${cfg.key}」未配置 Base API Key` });
    }
    const model = getModelRoute("quick") || cfg.model;
    const result = await runAgentTurnLoop({
      provider: cfg.protocol === "claude" ? "claude" : "openai",
      providerConfig: { baseUrl: cfg.baseUrl, apiKey: cfg.apiKey },
      model,
      systemPrompt: preset.system,
      userPrompt: preset.user(input),
      history: [],
      images: [],
      context: {},
      maxTurns: 1,
      temperature: 0.3,
      disableTools: true,
      onText: () => {},
      onToolStart: () => {},
      onToolResult: () => {}
    });
    const output = String(result.content || "").trim();
    recordTokenUsage({
      sessionId: null, agentId: null, mode: `text-${action}`,
      model, usage: result.usage
    });
    res.json({ success: true, action, title: preset.title, result: output, usage: result.usage || null });
  } catch (e) {
    res.status(500).json({ error: e?.message || "处理失败" });
  }
});

function getScreenshotDir() {
  return path2.join(getWorkspaceRoot(), "screenshots");
}

app.post("/api/tools/screenshot", async (req, res) => {
  try {
    const result = await executeTool("gui_screenshot", {}, {
      cwd: getWorkspaceRoot(),
      workspaceRoot: path2.resolve(__dirname2, ".."),
      permissionMode: "bypassPermissions",
      confirmed: true
    });
    if (result?.error) return res.status(400).json({ error: result.message || "截屏失败" });
    const filename = path2.basename(result.path);
    res.json({
      success: true,
      path: result.path,
      filename,
      width: result.width,
      height: result.height,
      url: `/api/screenshot/${encodeURIComponent(filename)}?t=${Date.now()}`
    });
  } catch (e) {
    res.status(500).json({ error: e?.message || "截屏失败" });
  }
});

app.get("/api/tools/screenshots", (req, res) => {
  try {
    const dir = getScreenshotDir();
    if (!fs2.existsSync(dir)) return res.json({ success: true, files: [] });
    const files = fs2.readdirSync(dir, { withFileTypes: true })
      .filter((d) => d.isFile() && /\.(png|jpe?g)$/i.test(d.name))
      .map((d) => {
        const abs = path2.join(dir, d.name);
        const stat = fs2.statSync(abs);
        return { name: d.name, size: stat.size, mtime: stat.mtime.toISOString(), url: `/api/screenshot/${encodeURIComponent(d.name)}?t=${stat.mtimeMs}` };
      })
      .sort((a, b) => b.mtime.localeCompare(a.mtime))
      .slice(0, 24);
    res.json({ success: true, files });
  } catch (e) {
    res.status(500).json({ error: e?.message || "读取截图失败" });
  }
});

app.get("/api/screenshot/:filename", (req, res) => {
  try {
    const dir = getScreenshotDir();
    const abs = path2.resolve(dir, String(req.params.filename || ""));
    const rel = path2.relative(dir, abs);
    if (!rel || rel.startsWith("..") || path2.isAbsolute(rel)) {
      return res.status(403).json({ error: "非法文件路径" });
    }
    if (!fs2.existsSync(abs)) return res.status(404).json({ error: "截图不存在" });
    res.sendFile(abs);
  } catch (e) {
    res.status(500).json({ error: e?.message || "读取截图失败" });
  }
});

// ==================== 编码搭档 ====================
const CODING_AGENTS = [
  { key: "claude-code", name: "Claude Code", command: "claude", template: 'claude -p "{goal}"', description: "Anthropic Claude Code CLI", docs: "https://docs.anthropic.com/en/docs/claude-code" },
  { key: "openai-codex", name: "OpenAI Codex", command: "codex", template: 'codex exec "{goal}"', description: "OpenAI Codex CLI", docs: "https://github.com/openai/codex" },
  { key: "opencode", name: "OpenCode", command: "opencode", template: 'opencode run "{goal}"', description: "开源编码 Agent CLI", docs: "https://github.com/sst/opencode" },
  { key: "qwen-code", name: "Qwen Code", command: "qwen", template: 'qwen -p "{goal}"', description: "通义千问编码 CLI", docs: "https://github.com/QwenLM/qwen-code" },
  { key: "kimi-code", name: "Kimi Code", command: "kimi", template: 'kimi -p "{goal}"', description: "Kimi 编码 CLI", docs: "https://github.com/MoonshotAI/kimi-cli" },
  { key: "custom", name: "自定义 CLI", command: "", template: "", description: "自己填写命令模板，支持 {goal} 与 {model} 占位符", docs: "" }
];
let codingAgentCache = { at: 0, list: null };
const codingRuns = new Map();

function findCodingAgent(key) {
  return CODING_AGENTS.find((a) => a.key === key) || null;
}
function detectCommand(cmd) {
  if (!cmd) return { installed: false, path: "", version: "" };
  try {
    const finder = process.platform === "win32" ? "where" : "which";
    const found = spawnSync(finder, [cmd], { encoding: "utf8", timeout: 4000, windowsHide: true });
    if (found.status !== 0) return { installed: false, path: "", version: "" };
    const cmdPath = String(found.stdout || "").split(/\r?\n/).map((s) => s.trim()).filter(Boolean)[0] || "";
    let version = "";
    try {
      const v = spawnSync(cmd, ["--version"], { encoding: "utf8", timeout: 4000, windowsHide: true, shell: true });
      version = String(v.stdout || v.stderr || "").trim().split(/\r?\n/)[0].slice(0, 120);
    } catch { /* 忽略 */ }
    return { installed: true, path: cmdPath, version };
  } catch {
    return { installed: false, path: "", version: "" };
  }
}
function detectCodingAgents(force = false) {
  if (!force && codingAgentCache.list && Date.now() - codingAgentCache.at < 60_000) {
    return codingAgentCache.list;
  }
  const list = CODING_AGENTS.map((a) => {
    const d = a.key === "custom" ? { installed: true, path: "", version: "" } : detectCommand(a.command);
    return { ...a, ...d };
  });
  codingAgentCache = { at: Date.now(), list };
  return list;
}
function resolveCodingCwd(input) {
  const raw = String(input || "").trim();
  const projectRoot = path2.resolve(__dirname2, "..");
  let target = raw;
  if (!raw || raw === "workspace") target = getWorkspaceRoot();
  else if (raw === "project") target = projectRoot;
  const abs = path2.resolve(target);
  try {
    if (!fs2.existsSync(abs) || !fs2.statSync(abs).isDirectory()) return null;
    return abs;
  } catch {
    return null;
  }
}
function quoteShellArg(value) {
  const s = String(value ?? "");
  if (process.platform === "win32") {
    return `"${s.replace(/"/g, '""').replace(/%/g, "%%")}"`;
  }
  return `'${s.replace(/'/g, `'\\''`)}'`;
}
function buildCodingCommand(template, goal, model) {
  let cmd = String(template || "").trim();
  if (!cmd) return "";
  if (cmd.includes("{goal}")) cmd = cmd.replace(/\{goal\}/g, quoteShellArg(goal));
  else cmd += ` ${quoteShellArg(goal)}`;
  cmd = cmd.replace(/\{model\}/g, quoteShellArg(model || ""));
  return cmd;
}
function getCodingProviderEnv(model) {
  const env = { ...process.env, AI_MODEL: model || "", CODING_GOAL: "" };
  try {
    const cfg = getActiveProviderConfig();
    env.AI_PROVIDER = cfg.key;
    env.CODING_GOAL = "";
    if (cfg.protocol === "claude") {
      env.ANTHROPIC_API_KEY = cfg.apiKey;
      env.ANTHROPIC_BASE_URL = cfg.baseUrl;
      env.ANTHROPIC_MODEL = model || cfg.model;
    } else {
      env.OPENAI_API_KEY = cfg.apiKey;
      env.OPENAI_BASE_URL = cfg.baseUrl;
      env.OPENAI_MODEL = model || cfg.model;
    }
  } catch { /* 忽略 */ }
  return env;
}
function broadcastCoding(run, payload) {
  for (const listener of run.listeners) {
    try { listener.write(`data: ${JSON.stringify(payload)}\n\n`); } catch { /* 忽略 */ }
  }
}
function createCodingNote(run) {
  try {
    const tail = String(run.output || "").slice(-6000);
    const content = [
      `# 编码任务：${run.goal}`,
      "",
      `- 代理：${run.agentName}`,
      `- 工作目录：${run.cwd}`,
      `- 命令：${run.command}`,
      `- 模型：${run.model || "-"}`,
      `- 状态：${run.status}${run.exitCode != null ? `（exit ${run.exitCode}）` : ""}`,
      `- 开始：${run.startedAt}`,
      `- 结束：${run.finishedAt || "-"}`,
      "",
      "## 输出摘要",
      "```text",
      tail || "（无输出）",
      "```"
    ].join("\n");
    return createNoteRecord({
      title: `编码任务：${String(run.goal).slice(0, 60)}`,
      content_md: content,
      tags: ["编码搭档", run.agentName],
      owner: "agent",
      visibility: "agent",
      task_id: null
    });
  } catch (e) {
    console.warn("[Coding] 写入笔记失败(忽略):", e?.message || e);
    return null;
  }
}

app.get("/api/coding/agents", (req, res) => {
  try {
    const agents = detectCodingAgents(req.query.refresh === "1");
    res.json({ success: true, agents });
  } catch (e) {
    res.status(500).json({ error: e?.message || "检测编码工具失败" });
  }
});

app.post("/api/coding/run", (req, res) => {
  try {
    const goal = String(req.body?.goal || "").trim();
    const agentKey = String(req.body?.agentKey || "").trim();
    const agent = findCodingAgent(agentKey);
    if (!goal) return res.status(400).json({ error: "任务目标不能为空" });
    if (!agent) return res.status(404).json({ error: "未知编码代理" });
    const cwd = resolveCodingCwd(req.body?.cwd);
    if (!cwd) return res.status(400).json({ error: "工作目录不存在" });
    const template = String(req.body?.commandTemplate || agent.template || "").trim();
    if (!template) return res.status(400).json({ error: "请填写命令模板" });
    const model = String(req.body?.model || getModelRoute("coding") || "").trim() || getActiveProviderConfig().model;
    const command = buildCodingCommand(template, goal, model);
    const id = `coding-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const startedAt = new Date().toISOString();
    const run = {
      id, agentKey: agent.key, agentName: agent.name, goal, cwd, command, model,
      status: "running", output: "", exitCode: null, startedAt, finishedAt: null,
      noteId: null, listeners: new Set(), child: null, stopping: false
    };
    db.prepare(`
      INSERT INTO coding_runs (id, agent_key, goal, cwd, command, model, status, output, exit_code, started_at, finished_at, note_id)
      VALUES (?, ?, ?, ?, ?, ?, 'running', '', NULL, ?, NULL, NULL)
    `).run(id, agent.key, goal, cwd, command, model, startedAt);
    codingRuns.set(id, run);
    const env = getCodingProviderEnv(model);
    env.CODING_GOAL = goal;
    let child;
    try {
      child = spawn(command, [], { cwd, shell: true, windowsHide: true, env });
    } catch (e) {
      run.status = "failed";
      run.output = `启动失败：${e?.message || e}`;
      run.finishedAt = new Date().toISOString();
      db.prepare("UPDATE coding_runs SET status=?, output=?, finished_at=? WHERE id=?").run(run.status, run.output, run.finishedAt, id);
      codingRuns.delete(id);
      return res.status(500).json({ error: run.output });
    }
    run.child = child;
    child.stdout?.on("data", (buf) => {
      const text = buf.toString();
      run.output = (run.output + text).slice(-200_000);
      broadcastCoding(run, { type: "output", stream: "stdout", text });
    });
    child.stderr?.on("data", (buf) => {
      const text = buf.toString();
      run.output = (run.output + text).slice(-200_000);
      broadcastCoding(run, { type: "output", stream: "stderr", text });
    });
    child.on("error", (e) => {
      run.output = (run.output + `\n[启动错误] ${e?.message || e}\n`).slice(-200_000);
      broadcastCoding(run, { type: "error", message: e?.message || String(e) });
    });
    child.on("close", (code) => {
      run.exitCode = code;
      run.finishedAt = new Date().toISOString();
      run.status = run.stopping ? "stopped" : (code === 0 ? "completed" : "failed");
      try {
        const note = createCodingNote(run);
        if (note) run.noteId = note.id;
        db.prepare("UPDATE coding_runs SET status=?, output=?, exit_code=?, finished_at=?, note_id=? WHERE id=?")
          .run(run.status, run.output, code, run.finishedAt, run.noteId, id);
        appendJournal({
          taskId: null, sessionId: null, eventType: "coding_run",
          summary: `编码任务${run.status === "completed" ? "完成" : run.status === "stopped" ? "已停止" : "失败"}：${run.goal.slice(0, 120)}`,
          details: { agent: run.agentName, cwd: run.cwd, command: run.command, exitCode: code, model: run.model },
          artifacts: { noteId: run.noteId }
        });
        createNotification({
          type: "coding",
          title: `编码任务${run.status === "completed" ? "完成" : run.status === "stopped" ? "已停止" : "失败"}：${run.goal.slice(0, 60)}`,
          body: `${run.agentName} · exit ${code}${run.noteId ? " · 已生成笔记" : ""}`,
          link: `coding:${run.id}`
        });
        sendWebhook("coding.finished", {
          runId: run.id,
          goal: run.goal,
          status: run.status,
          exitCode: code,
          noteId: run.noteId || null
        });
      } catch (e) {
        console.warn("[Coding] 收尾失败(忽略):", e?.message || e);
      }
      broadcastCoding(run, { type: "done", status: run.status, exitCode: code, noteId: run.noteId });
      for (const listener of run.listeners) {
        try { listener.end(); } catch { /* 忽略 */ }
      }
      run.listeners.clear();
      setTimeout(() => codingRuns.delete(id), 60_000).unref?.();
    });
    res.json({ success: true, runId: id, command, cwd, model, agent: agent.name });
  } catch (e) {
    res.status(500).json({ error: e?.message || "启动编码任务失败" });
  }
});

app.get("/api/coding/runs/:id/events", (req, res) => {
  const id = String(req.params.id || "");
  const run = codingRuns.get(id);
  if (!run) {
    const row = db.prepare("SELECT * FROM coding_runs WHERE id = ?").get(id);
    if (!row) return res.status(404).json({ error: "编码任务不存在" });
    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no"
    });
    res.write(`data: ${JSON.stringify({ type: "output", text: row.output || "" })}\n\n`);
    res.write(`data: ${JSON.stringify({ type: "done", status: row.status, exitCode: row.exit_code, noteId: row.note_id })}\n\n`);
    res.end();
    return;
  }
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
    "X-Accel-Buffering": "no"
  });
  res.write(`data: ${JSON.stringify({ type: "output", text: run.output })}\n\n`);
  res.write(`data: ${JSON.stringify({ type: "status", status: run.status })}\n\n`);
  run.listeners.add(res);
  const heartbeat = setInterval(() => {
    try { res.write(": ping\n\n"); } catch { /* 忽略 */ }
  }, 15_000);
  req.on("close", () => {
    clearInterval(heartbeat);
    run.listeners.delete(res);
  });
});

app.post("/api/coding/runs/:id/stop", (req, res) => {
  const run = codingRuns.get(String(req.params.id || ""));
  if (!run) return res.status(404).json({ error: "编码任务不存在或已结束" });
  run.stopping = true;
  try {
    if (process.platform === "win32" && run.child?.pid) {
      spawn("taskkill", ["/pid", String(run.child.pid), "/T", "/F"], { windowsHide: true });
    } else {
      run.child?.kill("SIGTERM");
    }
    broadcastCoding(run, { type: "status", status: "stopping" });
    res.json({ success: true });
  } catch (e) {
    res.status(500).json({ error: e?.message || "停止失败" });
  }
});

app.get("/api/coding/runs", (req, res) => {
  try {
    const rows = db.prepare("SELECT * FROM coding_runs ORDER BY started_at DESC LIMIT 30").all();
    const runs = rows.map((r) => {
      const active = codingRuns.get(r.id);
      return {
        ...r,
        status: active ? active.status : r.status,
        active: Boolean(active)
      };
    });
    res.json({ success: true, runs });
  } catch (e) {
    res.status(500).json({ error: e?.message || "读取编码任务失败" });
  }
});

// ==================== 笔记与 AI 工作日志 ====================
app.get("/api/notes", (req, res) => {
  try {
    const q = String(req.query.q || "").trim();
    const owner = String(req.query.owner || "").trim();
    const visibility = String(req.query.visibility || "").trim();
    const limit = Math.min(Math.max(Number(req.query.limit) || 200, 1), 500);
    const clauses = [];
    const params = [];
    if (q) {
      clauses.push("(title LIKE ? OR content_md LIKE ?)");
      params.push(`%${q}%`, `%${q}%`);
    }
    if (owner) { clauses.push("owner = ?"); params.push(owner); }
    if (visibility) { clauses.push("visibility = ?"); params.push(visibility); }
    const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
    const rows = db.prepare(`SELECT * FROM notes ${where} ORDER BY pinned DESC, updated_at DESC LIMIT ?`).all(...params, limit);
    res.json({ success: true, notes: rows.map(toNote), count: rows.length });
  } catch (e) {
    res.status(500).json({ error: e?.message || "读取笔记失败" });
  }
});

app.get("/api/notes/export", (req, res) => {
  try {
    const rows = db.prepare("SELECT * FROM notes ORDER BY updated_at DESC").all().map(toNote);
    res.setHeader("Content-Disposition", 'attachment; filename="ai-temple-notes.json"');
    res.json({ version: 1, exportedAt: new Date().toISOString(), notes: rows });
  } catch (e) {
    res.status(500).json({ error: e?.message || "导出笔记失败" });
  }
});

app.post("/api/notes/import", (req, res) => {
  try {
    const items = Array.isArray(req.body) ? req.body : (Array.isArray(req.body?.notes) ? req.body.notes : []);
    if (items.length === 0) return res.status(400).json({ error: "没有可导入的笔记" });
    let added = 0, updated = 0, skipped = 0;
    const now = new Date().toISOString();
    const upsert = db.transaction((list) => {
      for (const item of list.slice(0, 500)) {
        const title = String(item?.title || "").trim();
        const content = String(item?.content_md ?? item?.content ?? "").trim();
        if (!title || !content) { skipped++; continue; }
        const tags = JSON.stringify(Array.isArray(item.tags) ? item.tags.slice(0, 30).map(String) : []);
        const visibility = ["private", "shared", "agent"].includes(item.visibility) ? item.visibility : "shared";
        const owner = item.owner === "agent" ? "agent" : "human";
        const existing = item.id ? db.prepare("SELECT id FROM notes WHERE id = ?").get(String(item.id)) : null;
        if (existing) {
          db.prepare(`UPDATE notes SET title=?, content_md=?, tags=?, owner=?, visibility=?, task_id=?, pinned=?, updated_at=? WHERE id=?`)
            .run(title.slice(0, 200), content, tags, owner, visibility, item.task_id || null, item.pinned ? 1 : 0, now, existing.id);
          updated++;
        } else {
          const id = item.id ? String(item.id) : `note-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
          db.prepare(`INSERT INTO notes (id,title,content_md,tags,owner,visibility,task_id,pinned,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)`)
            .run(id, title.slice(0, 200), content, tags, owner, visibility, item.task_id || null, item.pinned ? 1 : 0, item.created_at || now, now);
          added++;
        }
      }
    });
    upsert(items);
    res.json({ success: true, added, updated, skipped });
  } catch (e) {
    res.status(500).json({ error: e?.message || "导入笔记失败" });
  }
});

app.get("/api/notes/:id", (req, res) => {
  try {
    const note = getNoteRecord(req.params.id);
    if (!note) return res.status(404).json({ error: "笔记不存在" });
    res.json({ success: true, note });
  } catch (e) {
    res.status(500).json({ error: e?.message || "读取笔记失败" });
  }
});

app.post("/api/notes", (req, res) => {
  try {
    const title = String(req.body?.title || "").trim();
    const content = String(req.body?.content_md ?? req.body?.content ?? "");
    if (!title) return res.status(400).json({ error: "标题不能为空" });
    const note = createNoteRecord({
      title, content_md: content,
      tags: Array.isArray(req.body?.tags) ? req.body.tags : [],
      owner: req.body?.owner === "agent" ? "agent" : "human",
      visibility: req.body?.visibility || "shared",
      task_id: req.body?.task_id || null,
      pinned: Boolean(req.body?.pinned)
    });
    res.json({ success: true, note });
  } catch (e) {
    res.status(500).json({ error: e?.message || "新建笔记失败" });
  }
});

app.patch("/api/notes/:id", (req, res) => {
  try {
    const existing = getNoteRecord(req.params.id);
    if (!existing) return res.status(404).json({ error: "笔记不存在" });
    const title = req.body?.title !== undefined ? String(req.body.title || "").trim() : existing.title;
    if (!title) return res.status(400).json({ error: "标题不能为空" });
    const content = req.body?.content_md !== undefined
      ? String(req.body.content_md || "")
      : (req.body?.content !== undefined ? String(req.body.content || "") : existing.content_md);
    const tags = req.body?.tags !== undefined && Array.isArray(req.body.tags)
      ? JSON.stringify(req.body.tags.slice(0, 30).map(String))
      : JSON.stringify(existing.tags);
    const visibility = ["private", "shared", "agent"].includes(req.body?.visibility) ? req.body.visibility : existing.visibility;
    const pinned = req.body?.pinned !== undefined ? (req.body.pinned ? 1 : 0) : (existing.pinned ? 1 : 0);
    const taskId = req.body?.task_id !== undefined ? (req.body.task_id || null) : existing.task_id;
    db.prepare(`UPDATE notes SET title=?, content_md=?, tags=?, visibility=?, task_id=?, pinned=?, updated_at=? WHERE id=?`)
      .run(title.slice(0, 200), content, tags, visibility, taskId, pinned, new Date().toISOString(), existing.id);
    res.json({ success: true, note: getNoteRecord(existing.id) });
  } catch (e) {
    res.status(500).json({ error: e?.message || "保存笔记失败" });
  }
});

app.delete("/api/notes/:id", (req, res) => {
  try {
    const result = db.prepare("DELETE FROM notes WHERE id = ?").run(String(req.params.id || ""));
    if (result.changes === 0) return res.status(404).json({ error: "笔记不存在" });
    res.json({ success: true });
  } catch (e) {
    res.status(500).json({ error: e?.message || "删除笔记失败" });
  }
});

function parseMaybeJson(value) {
  if (value == null) return null;
  try { return JSON.parse(value); } catch { return value; }
}

app.get("/api/journal", (req, res) => {
  try {
    const taskId = String(req.query.task_id || "").trim();
    const sessionId = String(req.query.session_id || "").trim();
    const limit = Math.min(Math.max(Number(req.query.limit) || 100, 1), 500);
    const clauses = [];
    const params = [];
    if (taskId) { clauses.push("task_id = ?"); params.push(taskId); }
    if (sessionId) { clauses.push("session_id = ?"); params.push(sessionId); }
    const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
    const rows = db.prepare(`SELECT * FROM agent_journal ${where} ORDER BY created_at DESC LIMIT ?`).all(...params, limit);
    res.json({
      success: true,
      entries: rows.map((r) => ({
        ...r,
        details: parseMaybeJson(r.details),
        artifacts: parseMaybeJson(r.artifacts)
      })),
      count: rows.length
    });
  } catch (e) {
    res.status(500).json({ error: e?.message || "读取工作日志失败" });
  }
});

app.post("/api/journal", (req, res) => {
  try {
    const id = appendJournal({
      taskId: req.body?.task_id || req.body?.taskId || null,
      sessionId: req.body?.session_id || req.body?.sessionId || null,
      eventType: req.body?.event_type || req.body?.eventType || "note",
      summary: req.body?.summary || "",
      details: req.body?.details || null,
      artifacts: req.body?.artifacts || null
    });
    if (!id) return res.status(500).json({ error: "写入工作日志失败" });
    res.json({ success: true, id });
  } catch (e) {
    res.status(500).json({ error: e?.message || "写入工作日志失败" });
  }
});

// ==================== 插件声明式工具 ====================
app.get("/api/plugins/tools", (req, res) => {
  try {
    const built = getPluginToolDefs();
    const tools = [...built.index.entries()].map(([name, v]) => ({
      name,
      plugin: v.pluginName,
      tool: v.tool?.name,
      description: v.tool?.description || '',
      readOnly: Boolean(v.tool?.readOnly),
      execution: v.tool?.execution?.type || 'unknown'
    }));
    res.json({ success: true, tools, count: tools.length });
  } catch (e) {
    res.status(500).json({ error: e?.message || "读取失败" });
  }
});

// 直接调用插件工具（连接验证 / 调试）
app.post("/api/plugins/:id/test", async (req, res) => {
  try {
    const built = getPluginToolDefs();
    const itemId = req.params.id;
    const toolName = String(req.body?.tool || "");
    const entry = [...built.index.entries()].find(([, v]) => v.pluginId === itemId && v.tool?.name === toolName)
      || [...built.index.entries()].find(([, v]) => v.pluginId === itemId);
    if (!entry) return res.status(400).json({ error: "插件未启用或工具不存在" });
    const result = await callPluginTool(entry[1], req.body?.arguments || {}, {
      workspaceRoot: resolveAgentCwd(null),
      sandboxRoot: sandboxEnabled() ? getSandboxRoot() : null
    });
    res.json({ success: !result.error, result });
  } catch (e) {
    res.status(500).json({ error: e?.message || "调用失败" });
  }
});

// ==================== MCP 运行时 ====================
app.get("/api/mcp/status", (req, res) => {
  try {
    res.json({ success: true, servers: mcpManager.statusAll(), toolCount: getMcpToolDefs().count });
  } catch (e) {
    res.status(500).json({ error: e?.message || "读取失败" });
  }
});

app.post("/api/mcp/:id/connect", async (req, res) => {
  try {
    const item = db.prepare("SELECT * FROM store_items WHERE id = ? AND kind = 'mcp'").get(req.params.id);
    if (!item) return res.status(404).json({ error: "MCP 条目不存在" });
    const cfg = (() => { try { return JSON.parse(item.config || "{}"); } catch { return {}; } })();
    const status = await mcpManager.connect({ ...item, config: cfg });
    res.json({ success: true, status, toolCount: getMcpToolDefs().count });
  } catch (e) {
    res.status(500).json({ error: e?.message || "连接失败" });
  }
});

app.post("/api/mcp/:id/disconnect", async (req, res) => {
  try {
    await mcpManager.disconnect(req.params.id);
    res.json({ success: true, toolCount: getMcpToolDefs().count });
  } catch (e) {
    res.status(500).json({ error: e?.message || "断开失败" });
  }
});

// 直接调用某个 MCP 条目下的工具（用于连接验证与调试）
app.post("/api/mcp/:id/call", async (req, res) => {
  try {
    const itemId = req.params.id;
    const toolName = String(req.body?.tool || "");
    const args = req.body?.arguments || {};
    const entry = [...mcpManager.toolIndex.entries()].find(([, v]) => v.itemId === itemId && v.originalName === toolName)
      || [...mcpManager.toolIndex.entries()].find(([, v]) => v.itemId === itemId);
    if (!entry) return res.status(400).json({ error: "该服务器未连接或工具不存在" });
    const result = await mcpManager.call(entry[0], args);
    res.json({ success: !result.error, result });
  } catch (e) {
    res.status(500).json({ error: e?.message || "调用失败" });
  }
});

app.post("/api/mcp/reconnect", async (req, res) => {
  try {
    await mcpManager.disconnectAll();
    const results = await mcpManager.connectEnabled();
    res.json({ success: true, results, toolCount: getMcpToolDefs().count });
  } catch (e) {
    res.status(500).json({ error: e?.message || "重连失败" });
  }
});

// ==================== 沙箱（文件级试错区） ====================
function listSandboxFiles(root) {
  const out = [];
  if (!fs.existsSync(root)) return out;
  const walk = (dir) => {
    for (const item of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, item.name);
      if (item.isDirectory()) walk(full);
      else {
        const stat = fs.statSync(full);
        out.push({
          path: path.relative(root, full).replace(/\\/g, "/"),
          size: stat.size,
          mtime: stat.mtime.toISOString()
        });
      }
    }
  };
  walk(root);
  return out;
}

app.get("/api/sandbox/status", (req, res) => {
  try {
    const root = getSandboxRoot();
    const files = listSandboxFiles(root);
    res.json({
      success: true,
      enabled: sandboxEnabled(),
      sandboxRoot: root,
      workspaceRoot: resolveAgentCwd(null),
      files,
      fileCount: files.length
    });
  } catch (e) {
    res.status(500).json({ error: e?.message || "读取失败" });
  }
});

app.post("/api/sandbox/toggle", (req, res) => {
  try {
    const enabled = Boolean(req.body?.enabled);
    process.env.SANDBOX_MODE = enabled ? "1" : "0";
    try {
      const envPath = path.join(__dirname, "..", ".env");
      let lines = fs.existsSync(envPath) ? fs.readFileSync(envPath, "utf-8").split(/\r?\n/) : [];
      const idx = lines.findIndex((l) => l.trim().startsWith("SANDBOX_MODE="));
      if (idx >= 0) lines[idx] = `SANDBOX_MODE=${enabled ? "1" : "0"}`;
      else lines.push(`SANDBOX_MODE=${enabled ? "1" : "0"}`);
      fs.writeFileSync(envPath, lines.filter((l) => l.trim()).join("\n") + "\n", "utf-8");
    } catch (e) {
      console.warn("[Sandbox] .env 写入失败(仅内存生效):", e?.message || e);
    }
    res.json({ success: true, enabled });
  } catch (e) {
    res.status(500).json({ error: e?.message || "切换失败" });
  }
});

app.post("/api/sandbox/commit", (req, res) => {
  try {
    const sandboxRoot = getSandboxRoot();
    const workspaceRoot = resolveAgentCwd(null);
    const files = listSandboxFiles(sandboxRoot);
    let committed = 0;
    for (const f of files) {
      const src = path.join(sandboxRoot, f.path);
      const dest = path.join(workspaceRoot, f.path);
      const rel = path.relative(workspaceRoot, dest);
      if (rel.startsWith("..") || path.isAbsolute(rel)) continue; // 越界保护
      fs.mkdirSync(path.dirname(dest), { recursive: true });
      fs.copyFileSync(src, dest);
      committed++;
    }
    res.json({ success: true, committed, files: files.length });
  } catch (e) {
    res.status(500).json({ error: e?.message || "提交失败" });
  }
});

app.post("/api/sandbox/discard", (req, res) => {
  try {
    const root = getSandboxRoot();
    if (fs.existsSync(root)) {
      for (const item of fs.readdirSync(root)) {
        fs.rmSync(path.join(root, item), { recursive: true, force: true });
      }
    }
    res.json({ success: true });
  } catch (e) {
    res.status(500).json({ error: e?.message || "清空失败" });
  }
});

// ==================== 数据库管理 ====================
function listDbTables() {
  const rows = db.prepare(
    "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name"
  ).all();
  return rows
    .filter((r) => !/(_fts|_data|_idx|_content|_docsize|_config)$/.test(r.name))
    .map((r) => {
      let count = 0;
      let columns = [];
      try {
        count = db.prepare(`SELECT COUNT(*) AS c FROM "${r.name.replace(/"/g, '""')}"`).get().c;
        columns = db.prepare(`PRAGMA table_info("${r.name.replace(/"/g, '""')}")`).all();
      } catch { /* 忽略单表读取失败 */ }
      return { name: r.name, type: r.type, rows: count, columns };
    });
}

app.get("/api/db/tables", (req, res) => {
  try {
    const tables = listDbTables();
    const pageSize = db.pragma("page_size", { simple: true });
    const pageCount = db.pragma("page_count", { simple: true });
    res.json({
      success: true,
      tables,
      stats: {
        tableCount: tables.length,
        totalRows: tables.reduce((s, t) => s + (t.rows || 0), 0),
        sizeBytes: pageSize * pageCount,
        walMode: db.pragma("journal_mode", { simple: true })
      }
    });
  } catch (e) {
    res.status(500).json({ error: e?.message || "读取失败" });
  }
});

app.get("/api/db/table/:name", (req, res) => {
  try {
    const name = String(req.params.name);
    const allowed = listDbTables().some((t) => t.name === name);
    if (!allowed) return res.status(400).json({ error: "表名不合法" });
    const limit = Math.min(Math.max(Number(req.query.limit) || 50, 1), 200);
    const offset = Math.max(Number(req.query.offset) || 0, 0);
    const quoted = `"${name.replace(/"/g, '""')}"`;
    const rows = db.prepare(`SELECT * FROM ${quoted} LIMIT ? OFFSET ?`).all(limit, offset);
    const columns = db.prepare(`PRAGMA table_info(${quoted})`).all().map((c) => c.name);
    res.json({ success: true, table: name, columns, rows, limit, offset });
  } catch (e) {
    res.status(500).json({ error: e?.message || "读取失败" });
  }
});

app.post("/api/db/query", (req, res) => {
  try {
    let sql = String(req.body?.sql || "").trim();
    if (!sql) return res.status(400).json({ error: "SQL 不能为空" });
    if (sql.endsWith(";")) sql = sql.slice(0, -1).trim();
    if (sql.includes(";")) return res.status(400).json({ error: "只允许单条语句" });
    if (!/^(select|with|pragma)\b/i.test(sql)) {
      return res.status(400).json({ error: "只允许 SELECT / WITH / PRAGMA 只读查询" });
    }
    const stmt = db.prepare(sql);
    if (!stmt.reader) return res.status(400).json({ error: "该语句不返回结果集" });
    const rows = stmt.all().slice(0, 200);
    const columns = rows.length > 0 ? Object.keys(rows[0]) : (stmt.columns?.() || []).map((c) => c.name);
    res.json({ success: true, columns, rows, rowCount: rows.length });
  } catch (e) {
    res.status(500).json({ error: e?.message || "查询失败" });
  }
});

app.post("/api/db/maintenance", (req, res) => {
  try {
    const op = String(req.body?.op || "");
    if (op === "checkpoint") {
      const r = db.pragma("wal_checkpoint(TRUNCATE)");
      return res.json({ success: true, op, result: r });
    }
    if (op === "vacuum") {
      db.exec("VACUUM");
      return res.json({ success: true, op, message: "VACUUM 完成，数据库已压缩" });
    }
    if (op === "integrity") {
      const r = db.pragma("integrity_check");
      return res.json({ success: true, op, result: r });
    }
    if (op === "analyze") {
      db.exec("ANALYZE");
      return res.json({ success: true, op, message: "ANALYZE 完成，查询统计已更新" });
    }
    res.status(400).json({ error: "不支持的操作" });
  } catch (e) {
    res.status(500).json({ error: e?.message || "维护失败" });
  }
});

app.get("/api/db/backup", (req, res) => {
  let tmpPath;
  try {
    db.pragma("wal_checkpoint(TRUNCATE)");
    const backupDir = path.join(__dirname, "..", "data", "backups");
    if (!fs.existsSync(backupDir)) fs.mkdirSync(backupDir, { recursive: true });
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    tmpPath = path.join(backupDir, `chat-${stamp}.db`);
    fs.copyFileSync(dbPath, tmpPath);
    res.download(tmpPath, `chat-backup-${stamp}.db`);
  } catch (e) {
    res.status(500).json({ error: e?.message || "备份失败" });
  }
});

// ==================== 知识图谱 ====================
function parseKgProps(v) {
  try { return v ? JSON.parse(v) : {}; } catch { return {}; }
}

app.get("/api/graph", (req, res) => {
  try {
    const entities = db.prepare("SELECT * FROM knowledge_graph ORDER BY created_at DESC").all()
      .map((e) => ({ ...e, properties: parseKgProps(e.properties) }));
    const edges = db.prepare(`
      SELECT e.*, s.entity_name AS source_name, t.entity_name AS target_name
      FROM knowledge_edges e
      LEFT JOIN knowledge_graph s ON s.id = e.source_entity_id
      LEFT JOIN knowledge_graph t ON t.id = e.target_entity_id
      ORDER BY e.created_at DESC
    `).all().map((e) => ({ ...e, properties: parseKgProps(e.properties) }));
    const typeRows = db.prepare("SELECT entity_type, COUNT(*) AS c FROM knowledge_graph GROUP BY entity_type").all();
    res.json({ success: true, entities, edges, stats: { entities: entities.length, edges: edges.length, byType: typeRows } });
  } catch (error) {
    res.status(500).json({ error: error?.message || "图谱读取失败" });
  }
});

app.post("/api/graph/entities", (req, res) => {
  try {
    const { id = null, name, type = "concept", properties = {} } = req.body || {};
    const entityName = String(name || "").trim();
    if (!entityName) return res.status(400).json({ error: "实体名称不能为空" });
    const now = new Date().toISOString();
    let targetId = id ? String(id) : null;
    if (!targetId) {
      const hit = db.prepare("SELECT id FROM knowledge_graph WHERE entity_name = ? AND entity_type = ?").get(entityName, type);
      targetId = hit?.id || `kg-${uuidv44().slice(0, 8)}`;
    }
    db.prepare(`
      INSERT INTO knowledge_graph (id, entity_name, entity_type, properties, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET entity_name = excluded.entity_name, entity_type = excluded.entity_type,
        properties = excluded.properties, updated_at = excluded.updated_at
    `).run(targetId, entityName, String(type || "concept"), JSON.stringify(properties || {}), now, now);
    res.json({ success: true, entity: { id: targetId, entity_name: entityName, entity_type: type } });
  } catch (error) {
    res.status(500).json({ error: error?.message || "实体保存失败" });
  }
});

app.delete("/api/graph/entities/:id", (req, res) => {
  try {
    const id = req.params.id;
    db.prepare("DELETE FROM knowledge_edges WHERE source_entity_id = ? OR target_entity_id = ?").run(id, id);
    const r = db.prepare("DELETE FROM knowledge_graph WHERE id = ?").run(id);
    res.json({ success: r.changes > 0 });
  } catch (error) {
    res.status(500).json({ error: error?.message || "实体删除失败" });
  }
});

app.post("/api/graph/edges", (req, res) => {
  try {
    const { sourceId, targetId, sourceName, targetName, type = "关联", properties = {} } = req.body || {};
    const now = new Date().toISOString();
    const resolveId = (id, name) => {
      if (id) return String(id);
      const e = db.prepare("SELECT id FROM knowledge_graph WHERE entity_name = ?").get(String(name || ""));
      return e?.id || null;
    };
    const sid = resolveId(sourceId, sourceName);
    const tid = resolveId(targetId, targetName);
    if (!sid || !tid) return res.status(400).json({ error: "源实体或目标实体不存在" });
    if (sid === tid) return res.status(400).json({ error: "不能连接自身" });
    const dup = db.prepare(
      "SELECT id FROM knowledge_edges WHERE source_entity_id = ? AND target_entity_id = ? AND relationship_type = ?"
    ).get(sid, tid, type);
    if (dup) return res.json({ success: true, edge: { id: dup.id }, duplicated: true });
    const id = `kge-${uuidv44().slice(0, 8)}`;
    db.prepare(`
      INSERT INTO knowledge_edges (id, source_entity_id, target_entity_id, relationship_type, properties, created_at)
      VALUES (?, ?, ?, ?, ?, ?)
    `).run(id, sid, tid, String(type || "关联"), JSON.stringify(properties || {}), now);
    res.json({ success: true, edge: { id, source_entity_id: sid, target_entity_id: tid, relationship_type: type } });
  } catch (error) {
    res.status(500).json({ error: error?.message || "关系保存失败" });
  }
});

app.delete("/api/graph/edges/:id", (req, res) => {
  try {
    const r = db.prepare("DELETE FROM knowledge_edges WHERE id = ?").run(req.params.id);
    res.json({ success: r.changes > 0 });
  } catch (error) {
    res.status(500).json({ error: error?.message || "关系删除失败" });
  }
});

// 从知识库重建图谱（保留手动创建的实体类型）
app.post("/api/graph/rebuild", (req, res) => {
  try {
    const faqRows = getAllFaq();
    const now = new Date().toISOString();
    const autoTypes = ["faq", "category", "keyword"];
    db.prepare(`DELETE FROM knowledge_edges WHERE source_entity_id IN (SELECT id FROM knowledge_graph WHERE entity_type IN (${autoTypes.map(() => "?").join(",")})) OR target_entity_id IN (SELECT id FROM knowledge_graph WHERE entity_type IN (${autoTypes.map(() => "?").join(",")}))`).run(...autoTypes, ...autoTypes);
    db.prepare(`DELETE FROM knowledge_graph WHERE entity_type IN (${autoTypes.map(() => "?").join(",")})`).run(...autoTypes);

    const upsertEntity = db.prepare(`
      INSERT INTO knowledge_graph (id, entity_name, entity_type, properties, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
    `);
    const uu = () => uuidv44().slice(0, 8);
    const entityIds = new Map();
    const ensureEntity = (name, type, props) => {
      const key = `${type}:${name}`;
      if (entityIds.has(key)) return entityIds.get(key);
      const id = `kg-${uu()}`;
      upsertEntity.run(id, String(name).slice(0, 100), type, JSON.stringify(props || {}), now, now);
      entityIds.set(key, id);
      return id;
    };
    const addEdge = db.prepare(`
      INSERT INTO knowledge_edges (id, source_entity_id, target_entity_id, relationship_type, properties, created_at)
      VALUES (?, ?, ?, ?, '{}', ?)
    `);
    let edges = 0;
    for (const f of faqRows) {
      const faqId = ensureEntity(f.question, "faq", { faqId: f.id, category: f.category, hits: f.hits });
      const catId = ensureEntity(f.category || "通用", "category", {});
      addEdge.run(`kge-${uu()}`, faqId, catId, "属于", now);
      edges++;
      let keywords = [];
      try {
        const parsed = JSON.parse(f.keywords || "[]");
        keywords = Array.isArray(parsed) ? parsed : String(parsed).split(/[,，\s]+/);
      } catch {
        keywords = String(f.keywords || "").split(/[,，\s]+/);
      }
      for (const kw of keywords.map((k) => String(k).trim()).filter(Boolean).slice(0, 5)) {
        const kwId = ensureEntity(kw, "keyword", {});
        addEdge.run(`kge-${uu()}`, faqId, kwId, "关键词", now);
        edges++;
      }
    }
    res.json({ success: true, faqCount: faqRows.length, entityCount: entityIds.size, edgeCount: edges });
  } catch (error) {
    res.status(500).json({ error: error?.message || "图谱重建失败" });
  }
});

// ==================== 工作区文件浏览（主界面 @ 引用） ====================
const REF_TEXT_EXTS = new Set([
  ".txt", ".md", ".json", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
  ".html", ".css", ".py", ".java", ".cs", ".go", ".rs", ".sql", ".yaml",
  ".yml", ".xml", ".csv", ".log", ".bat", ".ps1", ".sh", ".ini", ".toml"
]);

app.get("/api/workspace/files", (req, res) => {
  try {
    const root = getWorkspaceRoot();
    const q = String(req.query.q || "").toLowerCase();
    const results = [];
    const walk = (dir, rel, level) => {
      if (level > 2 || results.length >= 300) return;
      let entries = [];
      try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
      entries.sort((a, b) => a.name.localeCompare(b.name));
      for (const entry of entries) {
        if (results.length >= 300) break;
        if (entry.name.startsWith(".") || entry.name === "node_modules") continue;
        const abs = path.join(dir, entry.name);
        const relPath = rel ? `${rel}/${entry.name}` : entry.name;
        if (entry.isDirectory()) {
          walk(abs, relPath, level + 1);
          continue;
        }
        if (!REF_TEXT_EXTS.has(path.extname(entry.name).toLowerCase())) continue;
        if (q && !relPath.toLowerCase().includes(q)) continue;
        let size = 0;
        try { size = fs.statSync(abs).size; } catch { /* 忽略 */ }
        results.push({ path: relPath, size });
      }
    };
    walk(root, "", 0);
    res.json({ success: true, root, files: results });
  } catch (error) {
    res.status(500).json({ error: error?.message || "读取工作区失败" });
  }
});

app.get("/api/workspace/file", (req, res) => {
  try {
    const root = getWorkspaceRoot();
    const rel = String(req.query.path || "");
    if (!rel) return res.status(400).json({ error: "缺少文件路径" });
    const abs = path.resolve(root, rel);
    const relCheck = path.relative(root, abs);
    if (!relCheck || relCheck.startsWith("..") || path.isAbsolute(relCheck)) {
      return res.status(403).json({ error: "只能引用工作区内的文件" });
    }
    if (!REF_TEXT_EXTS.has(path.extname(abs).toLowerCase())) {
      return res.status(400).json({ error: "该文件类型不支持 @ 引用" });
    }
    const stat = fs.statSync(abs);
    if (stat.size > 512 * 1024) {
      return res.status(400).json({ error: "文件超过 512KB，暂不支持引用" });
    }
    const content = fs.readFileSync(abs, "utf8");
    res.json({ success: true, path: rel, size: stat.size, content: content.slice(0, 100 * 1024) });
  } catch (error) {
    res.status(404).json({ error: error?.message || "文件不存在" });
  }
});

// ==================== 自主任务检查点 ====================
function mapAutonomousTask(row) {
  let plan = [];
  let stepResults = [];
  try { plan = JSON.parse(row.plan || "[]"); } catch { plan = []; }
  try { stepResults = JSON.parse(row.step_results || "[]"); } catch { stepResults = []; }
  return {
    id: row.id,
    sessionId: row.session_id,
    goal: row.goal,
    plan,
    stepResults,
    totalSteps: row.total_steps,
    currentStep: row.current_step,
    progress: row.total_steps ? Math.round((row.current_step / row.total_steps) * 100) : 0,
    status: row.status,
    lastError: row.last_error,
    priority: row.priority || 0,
    dueAt: row.due_at || null,
    goalId: row.goal_id || null,
    origin: row.origin || "manual",
    replanCount: row.replan_count || 0,
    autoRetry: row.auto_retry || 0,
    maxRetry: row.max_retry ?? 2,
    timeoutSec: row.timeout_sec ?? 900,
    recurrence: row.recurrence || "none",
    recurTime: row.recur_time || "09:00",
    recurWeekday: row.recur_weekday ?? 1,
    recurMonthday: row.recur_monthday ?? 1,
    startedAt: row.started_at || null,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    completedAt: row.completed_at
  };
}

app.get("/api/autonomous/tasks", (req, res) => {
  try {
    const sessionId = req.query.sessionId ? String(req.query.sessionId) : null;
    const status = req.query.status ? String(req.query.status) : null;
    const conditions = [];
    const params = [];
    if (sessionId) { conditions.push("session_id = ?"); params.push(sessionId); }
    if (status) { conditions.push("status = ?"); params.push(status); }
    const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";
    const rows = db.prepare(`SELECT * FROM autonomous_tasks ${where} ORDER BY updated_at DESC LIMIT 30`).all(...params);
    res.json({ success: true, tasks: rows.map(mapAutonomousTask) });
  } catch (e) {
    res.status(500).json({ error: e?.message || "读取失败" });
  }
});

app.post("/api/autonomous/tasks/:id/abandon", (req, res) => {
  try {
    const r = db.prepare("UPDATE autonomous_tasks SET status = 'abandoned', updated_at = ? WHERE id = ?")
      .run(new Date().toISOString(), req.params.id);
    res.json({ success: r.changes > 0 });
  } catch (e) {
    res.status(500).json({ error: e?.message || "操作失败" });
  }
});

// ==================== 主动服务端点 ====================
app.get("/api/proactive/pending", async (req, res) => {
  try {
    const sessionId = req.query.sessionId ? String(req.query.sessionId) : null;
    if (sessionId) {
      // 24 小时内没分析过且无待办 → 现场跑一次主动分析
      const rows = db.prepare(
        "SELECT COUNT(*) AS c FROM proactive_services WHERE session_id = ? AND created_at > ?"
      ).get(sessionId, new Date(Date.now() - 864e5).toISOString());
      if (!rows || rows.c === 0) {
        const { ProactiveServiceEngine } = await import("./engine/proactive-service.mjs");
        const engine = new ProactiveServiceEngine(db, { sessionId });
        await engine.provideProactiveService(sessionId);
      }
      const items = db.prepare(
        "SELECT id, session_id, service_type, trigger_condition, action, status, priority, weight, created_at FROM proactive_services WHERE session_id = ? AND status = 'pending' ORDER BY priority DESC, weight DESC, created_at DESC LIMIT 10"
      ).all(sessionId);
      return res.json({ success: true, items });
    }
    const items = db.prepare(
      "SELECT id, session_id, service_type, trigger_condition, action, status, priority, weight, created_at FROM proactive_services WHERE status = 'pending' ORDER BY priority DESC, weight DESC, created_at DESC LIMIT 20"
    ).all();
    res.json({ success: true, items });
  } catch (error) {
    res.status(500).json({ error: error?.message || "获取失败" });
  }
});
app.post("/api/proactive/:id/resolve", (req, res) => {
  try {
    // 反馈学习：accepted（采纳/执行）或 dismissed（忽略），用于建议排序权重
    const feedback = String((req.body || {}).feedback || "accepted") === "dismissed" ? "dismissed" : "accepted";
    const status = feedback === "dismissed" ? "dismissed" : "executed";
    const now = new Date().toISOString();
    const r = db.prepare(
      "UPDATE proactive_services SET status = ?, feedback = ?, feedback_at = ?, executed_at = ? WHERE id = ?"
    ).run(status, feedback, now, feedback === "executed" ? now : null, req.params.id);
    res.json({ success: r.changes > 0, feedback });
  } catch (error) {
    res.status(500).json({ error: error?.message || "更新失败" });
  }
});

// ==================== L2 / L3 自主性端点 ====================
app.get("/api/autonomy/status", (req, res) => {
  try {
    const profile = userProfileService.get();
    res.json({
      success: true,
      patrol: proactivePatrol.status(),
      queue: taskQueue.status(),
      profile: profile ? { summary: profile.summary, topics: profile.topics, intents: profile.intents, updatedAt: profile.updatedAt } : null,
      strategyCount: db.prepare("SELECT COUNT(*) AS c FROM strategy_library").get()?.c || 0,
      goalActive: db.prepare("SELECT COUNT(*) AS c FROM agent_goals WHERE status = 'active'").get()?.c || 0
    });
  } catch (e) {
    res.status(500).json({ error: e?.message || "读取失败" });
  }
});

app.post("/api/autonomy/patrol", async (req, res) => {
  try {
    const result = await proactivePatrol.runOnce({ trigger: "manual" });
    res.json({ success: !result?.error, result });
  } catch (e) {
    res.status(500).json({ error: e?.message || "巡检失败" });
  }
});

app.post("/api/autonomy/patrol/config", (req, res) => {
  try {
    const { enabled, intervalMinutes } = req.body || {};
    const status = proactivePatrol.configure({
      enabled: enabled === undefined ? undefined : Boolean(enabled),
      intervalMinutes: intervalMinutes === undefined ? undefined : Number(intervalMinutes)
    });
    res.json({ success: true, ...status });
  } catch (e) {
    res.status(500).json({ error: e?.message || "配置失败" });
  }
});

app.get("/api/profile", (req, res) => {
  try {
    res.json({ success: true, profile: userProfileService.get() });
  } catch (e) {
    res.status(500).json({ error: e?.message || "读取失败" });
  }
});

app.post("/api/profile/rebuild", (req, res) => {
  try {
    const days = Number((req.body || {}).days) || 30;
    res.json({ success: true, profile: userProfileService.rebuild(Math.max(1, Math.min(365, days))) });
  } catch (e) {
    res.status(500).json({ error: e?.message || "重建失败" });
  }
});

app.get("/api/strategies", (req, res) => {
  try {
    res.json({ success: true, strategies: strategyLibrary.list(Number(req.query.limit) || 50) });
  } catch (e) {
    res.status(500).json({ error: e?.message || "读取失败" });
  }
});

app.delete("/api/strategies/:id", (req, res) => {
  try {
    res.json({ success: strategyLibrary.remove(req.params.id) });
  } catch (e) {
    res.status(500).json({ error: e?.message || "删除失败" });
  }
});

// v2.9 策略效果评估与自动淘汰
app.post("/api/strategies/evaluate", (req, res) => {
  try {
    const { minReuseCount = 10, minSuccessRate = 0.3, minAvgScore = 0.5 } = req.body || {};
    const result = strategyLibrary.evaluateAndPrune(
      Number(minReuseCount),
      Number(minSuccessRate),
      Number(minAvgScore)
    );
    res.json({ success: true, ...result });
  } catch (e) {
    res.status(500).json({ error: e?.message || "评估失败" });
  }
});

// 任务入队：后台调度执行（自动触发）
app.post("/api/autonomous/queue", (req, res) => {
  try {
    const {
      sessionId, goal, priority = 0, dueAt = null, goalId = null, model = null,
      allowWrites = true, recurrence = "none", recurTime = "09:00",
      recurWeekday = 1, recurMonthday = 1, maxRetry = 2, timeoutSec = 900
    } = req.body || {};
    if (!goal) return res.status(400).json({ error: "任务目标必填" });
    if (!sessionId) return res.status(400).json({ error: "会话 ID 必填" });
    const recur = ["none", "daily", "weekly", "monthly"].includes(recurrence) ? recurrence : "none";
    if (!/^\d{1,2}:\d{2}$/.test(String(recurTime || ""))) {
      return res.status(400).json({ error: "循环时间格式应为 HH:MM" });
    }
    const paddedTime = String(recurTime).padStart(5, "0");
    const weekday = Math.min(Math.max(Number(recurWeekday) || 0, 0), 6);
    const monthday = Math.min(Math.max(Number(recurMonthday) || 1, 1), 31);
    const nextDue = dueAt || (recur !== "none"
      ? computeNextRun({ recurrence: recur, recur_time: paddedTime, recur_weekday: weekday, recur_monthday: monthday }, new Date())
      : null);
    const task = taskQueue.enqueue({
      sessionId, goal,
      priority: Number(priority) || 0,
      dueAt: nextDue,
      goalId: goalId || null,
      model: model || null,
      allowWrites: allowWrites === false ? 0 : 1,
      recurrence: recur,
      recurTime: paddedTime,
      recurWeekday: weekday,
      recurMonthday: monthday,
      maxRetry: Math.min(Math.max(Number(maxRetry) || 0, 0), 5),
      timeoutSec: Math.min(Math.max(Number(timeoutSec) || 900, 60), 7200)
    });
    res.json({ success: true, task: mapAutonomousTask(task) });
  } catch (e) {
    res.status(500).json({ error: e?.message || "入队失败" });
  }
});

// 暂停/失败任务重新入队（手动重试）
app.post("/api/autonomous/tasks/:id/retry", (req, res) => {
  try {
    const ok = taskQueue.requeue(req.params.id);
    // 后台调度每 30 秒轮询一次；这里立即触发一次，避免等待
    setTimeout(() => taskQueue.tick().catch(() => {}), 20).unref?.();
    res.json({ success: ok });
  } catch (e) {
    res.status(500).json({ error: e?.message || "重试失败" });
  }
});

// ==================== 长程目标 ====================
app.get("/api/goals", (req, res) => {
  try {
    const status = req.query.status ? String(req.query.status) : null;
    res.json({ success: true, goals: goalService.list(status) });
  } catch (e) {
    res.status(500).json({ error: e?.message || "读取失败" });
  }
});

app.post("/api/goals", (req, res) => {
  try {
    const { title, description, acceptance, deadline, sessionId, priority } = req.body || {};
    res.json({ success: true, goal: goalService.create({ title, description, acceptance, deadline, sessionId, priority }) });
  } catch (e) {
    res.status(400).json({ error: e?.message || "创建失败" });
  }
});

app.patch("/api/goals/:id", (req, res) => {
  try {
    res.json({ success: true, goal: goalService.update(req.params.id, req.body || {}) });
  } catch (e) {
    res.status(500).json({ error: e?.message || "更新失败" });
  }
});

app.delete("/api/goals/:id", (req, res) => {
  try {
    res.json({ success: goalService.remove(req.params.id) });
  } catch (e) {
    res.status(500).json({ error: e?.message || "删除失败" });
  }
});

app.post("/api/goals/:id/review", async (req, res) => {
  try {
    const result = await goalService.review(req.params.id);
    res.json({ success: true, goal: result });
  } catch (e) {
    res.status(500).json({ error: e?.message || "验收失败" });
  }
});
app.get("/api/admin/auth", (req, res) => {
  res.json({ required: adminAuthRequired(), authed: isAdminAuthed(req), host: BIND_HOST });
});
app.post("/api/admin/login", (req, res) => {
  if (!adminAuthRequired()) {
    return res.json({ success: true, required: false, message: "当前未开启管理后台鉴权" });
  }
  const password = String((req.body || {}).password || "");
  if (!password || password !== String(process.env.ADMIN_PASSWORD || "")) {
    return res.status(401).json({ error: "管理密码错误，请重试" });
  }
  res.setHeader("Set-Cookie", adminCookieHeader());
  res.json({ success: true, required: true });
});
app.post("/api/admin/logout", (req, res) => {
  res.setHeader("Set-Cookie", "admin_token=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0");
  res.json({ success: true });
});
app.use("/api/admin", requireAdmin);
app.get("/api/admin/overview", (req, res) => {
  try {
    const stats = getAdminStats();
    const bySource = countIntentsBySource();
    const byType = countIntentsByType();
    const sourceMap = {};
    bySource.forEach((s) => {
      sourceMap[s.source] = s.count;
    });
    res.json({
      stats,
      intentSource: sourceMap,
      intentTypes: byType,
      recentSessions: getAdminSessions().slice(0, 8)
    });
  } catch (error) {
    res.status(500).json({ error: error?.message || "\u83B7\u53D6\u7EDF\u8BA1\u5931\u8D25" });
  }
});
app.get("/api/admin/sessions", (req, res) => {
  res.json({ sessions: getAdminSessions() });
});
app.delete("/api/admin/sessions/:id", (req, res) => {
  const success = deleteSession(req.params.id);
  res.json({ success });
});
app.get("/api/admin/messages", (req, res) => {
  const { sessionId, keyword, role, intent, source, limit, offset } = req.query;
  const messages = searchMessages({
    sessionId: sessionId ? String(sessionId) : void 0,
    keyword: keyword ? String(keyword) : void 0,
    role: role ? String(role) : void 0,
    intent: intent ? String(intent) : void 0,
    source: source ? String(source) : void 0,
    limit: limit ? Number(limit) : 100,
    offset: offset ? Number(offset) : 0
  });
  res.json({ messages });
});
app.get("/api/admin/intents", (req, res) => {
  const limit = req.query.limit ? Number(req.query.limit) : 100;
  res.json({ logs: getIntentLogs(limit) });
});
app.get("/api/admin/export", (req, res) => {
  const sessionId = req.query.sessionId ? String(req.query.sessionId) : void 0;
  const messages = searchMessages({ sessionId, limit: 1e3 });
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Content-Disposition", `attachment; filename="chat-export-${Date.now()}.json"`);
  res.end(JSON.stringify({ exportedAt: (/* @__PURE__ */ new Date()).toISOString(), messages }, null, 2));
});
var distDir = path2.join(__dirname2, "dist");
if (fs2.existsSync(distDir)) {
  app.use(express.static(distDir, {
    maxAge: "1h",
    index: false,
    // HTML 不缓存，避免 UI 更新后仍加载旧页面
    setHeaders: (res, filePath) => {
      if (String(filePath).toLowerCase().endsWith(".html")) {
        res.setHeader("Cache-Control", "no-cache, no-store, must-revalidate");
      }
    }
  }));
  app.get(["/admin/login", "/admin"], (req, res, next) => {
    if (!adminAuthRequired() || isAdminAuthed(req)) return next();
    res.type("html").send(buildAdminLoginPage());
  });
  app.get("/admin.html", (req, res) => {
    res.sendFile(path2.join(distDir, "admin.html"));
  });
  app.get("*", (req, res, next) => {
    if (req.path.startsWith("/api/")) return next();
    res.sendFile(path2.join(distDir, "index.html"));
  });
  console.log(`[Static] \u5DF2\u6258\u7BA1\u524D\u7AEF\u76EE\u5F55: ${distDir}`);
}
﻿const BOX   = "\u250C\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510";
const BOX_END = "\u2514\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2518";
var server = app.listen(PORT, BIND_HOST, () => {
  const shownHost = isLoopbackHost(BIND_HOST) ? "localhost" : BIND_HOST;
  const net = isLoopbackHost(BIND_HOST) ? "\u4EC5\u672C\u673A" : "\u5BF9\u5916\u5F00\u653E";
  console.log("");
  console.log(BOX);
  console.log("      \u667A\u80FD Agent \u7FA4 \u00B7 \u670D\u52A1\u5DF2\u542F\u52A8");
  console.log(`      \u5730\u5740: http://${shownHost}:${PORT}`);
  console.log(`      \u7ED1\u5B9A: ${BIND_HOST} \u00B7 ${net}`);
  console.log("      \u6570\u636E\u5E93: SQLite (data/chat.db)");
  console.log("      \u65E5\u5FD7: logs/server.log \u00B7 \u8FFD\u52A0\u5199\u5165\u4E0D\u8986\u76D6");
  console.log(BOX_END);
  console.log("");
  if (!isLoopbackHost(BIND_HOST)) {
    console.log(`[Net] \u5DF2\u5BF9\u5916\u5F00\u653E\uFF0C\u5C40\u57DF\u7F51\u53EF\u901A\u8FC7 http://<\u672C\u673AIP>:${PORT} \u8BBF\u95EE`);
    if (adminAuthRequired()) console.log("[Net] \u7BA1\u7406\u540E\u53F0 /admin \u9700\u8981\u5BC6\u7801\u767B\u5F55\uFF08\u89C1 .env \u7684 ADMIN_PASSWORD\uFF09");
  }
  // 启动后异步连接已启用的 MCP 服务器（失败不影响主服务）
  setTimeout(() => {
    mcpManager.connectEnabled()
      .then((results) => {
        const ok = results.filter((r) => r.connected).length;
        if (results.length > 0) console.log(`[MCP] 自动连接完成：${ok}/${results.length} 个服务器，工具 ${getMcpToolDefs().count} 个`);
      })
      .catch((e) => console.warn("[MCP] 自动连接失败(忽略):", e?.message || e));
  }, 1200);

  // L3：服务重启恢复中断任务 + 后台任务调度；L2：主动服务后台巡检
  try {
    const recovered = taskQueue.recoverOnStart();
    if (recovered > 0) console.log(`[Queue] 服务重启恢复 ${recovered} 个中断任务，已重新排队`);
    taskQueue.start();
  } catch (e) {
    console.warn("[Queue] 调度启动失败(忽略):", e?.message || e);
  }
  try {
    proactivePatrol.start();
  } catch (e) {
    console.warn("[Patrol] 巡检启动失败(忽略):", e?.message || e);
  }
});
function checkpointDb() {
  try {
    const r = db.pragma("wal_checkpoint(PASSIVE)");
    if (r && r[0] && r[0].busy !== 0) return;
  } catch (e) {
    console.warn("[DB] WAL checkpoint \u5931\u8D25(\u5FFD\u7565):", e?.message || e);
  }
}
var checkpointTimer = setInterval(checkpointDb, 6e4);
if (typeof checkpointTimer.unref === "function") checkpointTimer.unref();
// 长短期记忆定时清理：每小时删除已过期的上下文记忆
var memoryCleanupTimer = setInterval(() => {
  try {
    db.prepare("DELETE FROM context_memory WHERE expires_at IS NOT NULL AND expires_at < ?")
      .run(new Date().toISOString());
  } catch (e) {
    console.warn("[Memory] 记忆清理失败(忽略):", e?.message || e);
  }
}, 36e5);
if (typeof memoryCleanupTimer.unref === "function") memoryCleanupTimer.unref();
// 自动备份：启动后 45 秒检查一次，之后每 6 小时检查；每天最多一份
setTimeout(() => {
  runAutoBackupIfDue().catch((e) => console.warn("[Backup] 自动备份失败(忽略):", e?.message || e));
}, 45e3).unref?.();
var backupTimer = setInterval(() => {
  runAutoBackupIfDue().catch((e) => console.warn("[Backup] 自动备份失败(忽略):", e?.message || e));
}, 6 * 3600e3);
if (typeof backupTimer.unref === "function") backupTimer.unref();
function shutdown(signal) {
  console.log(`[Server] \u6536\u5230 ${signal}\uFF0C\u6B63\u5728\u9000\u51FA...`);
  let finished = false;
  const done = () => {
    if (finished) return;
    finished = true;
    clearInterval(checkpointTimer);
    clearInterval(memoryCleanupTimer);
    clearInterval(backupTimer);
    try { taskQueue.stop(); } catch { /* 忽略 */ }
    try { proactivePatrol.stop(); } catch { /* 忽略 */ }
    try {
      db.pragma("wal_checkpoint(TRUNCATE)");
      console.log("[DB] WAL \u5DF2\u5199\u56DE data/chat.db");
    } catch (e) {
      console.warn("[DB] WAL checkpoint \u5931\u8D25(\u5FFD\u7565):", e?.message || e);
    }
    try {
      db.close();
    } catch {
    }
    // 关闭 MCP 子进程，避免残留
    try { mcpManager.disconnectAll(); } catch { /* 忽略 */ }
    process.exit(0);
  };
  try {
    server.close(() => done());
    const t = setTimeout(done, 3e3);
    if (typeof t.unref === "function") t.unref();
  } catch {
    done();
  }
}
process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));
process.on("uncaughtException", (err) => {
  console.error("[Server] \u672A\u6355\u83B7\u5F02\u5E38(\u8FDB\u7A0B\u7EE7\u7EED\u8FD0\u884C):", err?.stack || err);
});
process.on("unhandledRejection", (reason) => {
  console.error("[Server] \u672A\u5904\u7406\u7684 Promise \u62D2\u7EDD(\u8FDB\u7A0B\u7EE7\u7EED\u8FD0\u884C):", reason);
});
