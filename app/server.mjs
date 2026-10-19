// AI 神殿 · 模块化主服务入口 (v2.9)
// 整合所有配置、中间件、路由、处理器、引擎服务

import express from "express";
import { fileURLToPath } from "url";
import { dirname, join, resolve } from "path";

import fs from "fs";
import crypto from "crypto";
import { v4 as uuidv4 } from "uuid";
import Database from "better-sqlite3";

// ==================== 核心配置导入 ====================
import {
  APP_VERSION,
  APP_NAME,
  BUILD_DATE,
  DEFAULT_VALUES,
  ERROR_CODES,
  HTTP_STATUS,
  REALM_THRESHOLDS,
  MEMORY_REALM_LAYERS,
  TRIAL_CONFIG,
  TRIAL_XP_BONUS,
  PERMISSION_ACTIONS
} from "./config/constants.mjs";

import { FEATURES, isEnabled, enable, disable, getAllFeatures } from "./config/feature-flags.mjs";
import { REALMS, PERMISSION_MATRIX, hasPermission as checkRealmPermission, getRealmConfig, getNextTrial, canPromote } from "./config/realms.mjs";

// ==================== 认证原语（单一真源，auth.mjs 亦从此导入） ====================
import {
  getFamilyPassword,
  familySessionToken,
  parseCookies,
  isFamilyAuthenticated,
  getBindHost,
  isLoopbackHost,
  isLoopbackRequest,
  adminAuthRequired,
  adminToken,
  isAdminAuthed,
  adminCookieHeader,
  isFamilyExemptPath,
  isDesktopExemptPath,
  isAdminExemptPath
} from "./config/auth-core.mjs";

// ==================== 中间件导入 ====================
import { errorHandler, notFoundHandler, asyncHandler, requestLogger } from "./middleware/error.mjs";
import { requireAdmin, requireFamily, requireOpenApiKey, setOpenApiDeps } from "./middleware/auth.mjs";
import { validateBody, validateParams, validateQuery, validators, buildValidator, commonSchemas } from "./middleware/validation.mjs";
import { oracleFilter, handleArbitration, getPendingList, getLists, manageLists, setSettingStore as setOracleSettingStore } from "./middleware/oracle-filter.mjs";
import { resourceGuard, getResourceQuotas, updateResourceQuota, getResourceUsage, getQuotaConfig, setQuotaConfig, startResourceMonitor, setSettingStore as setGuardSettingStore, setActiveTaskCounter } from "./middleware/resource-guard.mjs";

// ==================== 路由注册导入 ====================
import { registerSessionRoutes } from "./routes/sessions.mjs";
import { registerChatRoutes } from "./routes/chat.mjs";
import { registerMemoryRoutes } from "./routes/memory.mjs";
import { registerAutonomyRoutes } from "./routes/autonomy.mjs";
import { registerPantheonRoutes } from "./routes/pantheon.mjs";
import { registerToolsRoutes, registerSettingsRoutes, registerOpenRoutes } from "./routes/tools.mjs";
import { registerAdminRoutes, registerDesktopRoutes } from "./routes/admin.mjs";

// ==================== Handler 导入 ====================
import { sessionHandlers } from "./routes/sessions/handlers.mjs";
import { chatHandlers } from "./routes/chat/handlers.mjs";
import { memoryHandlers } from "./routes/memory/handlers.mjs";
import { autonomyHandlers } from "./routes/autonomy/handlers.mjs";
import { pantheonHandlers } from "./routes/pantheon/handlers.mjs";
import { toolsHandlers, settingsHandlers, openHandlers } from "./routes/tools/handlers.mjs";
import { adminHandlers, desktopHandlers } from "./routes/admin/handlers.mjs";

// ==================== 引擎服务导入 ====================
import { ensureStoreSeed, getEnabledStorePrompt, registerStoreEndpoints } from "./engine/store-service.mjs";
import { runDatabaseMigrations } from "./engine/db-migration.mjs";
import { UiaController, uiaController } from "./engine/uia.mjs";
import { callOpenAIStream, callClaudeStream, callOllamaStream, runAgentTurnLoop } from "./engine/providers.mjs";
import { TOOLS_OPENAI, TOOLS_CLAUDE, executeTool, getEnabledTools } from "./engine/tools.mjs";
import { classifyModel } from "./engine/model-capabilities.mjs";
import {
  buildCodingCommand, resolveCodingCwd, findCodingAgent, detectCodingAgents,
  getCodingProviderEnv, quoteShellArg
} from "./engine/coding.mjs";
import {
  createNotesService, notesToolApi, bindNotesService,
  createNoteRecord, getNoteRecord, searchNoteRecords, toNote
} from "./engine/notes.mjs";
import {
  createJournalService, bindJournalService, appendJournal, parseMaybeJson
} from "./engine/journal.mjs";

// ==================== 环境初始化 ====================
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const PROJECT_ROOT = resolve(__dirname, "..");
const DATA_DIR = join(PROJECT_ROOT, "data");
const LOGS_DIR = join(PROJECT_ROOT, "logs");
const DIST_DIR = join(__dirname, "dist");
const BACKUP_ROOT = join(DATA_DIR, "backups");
const WORKSPACE_ROOT = join(PROJECT_ROOT, "workspace");
const SCREENSHOT_DIR = join(WORKSPACE_ROOT, "screenshots");

if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
if (!fs.existsSync(LOGS_DIR)) fs.mkdirSync(LOGS_DIR, { recursive: true });
if (!fs.existsSync(WORKSPACE_ROOT)) fs.mkdirSync(WORKSPACE_ROOT, { recursive: true });
if (!fs.existsSync(SCREENSHOT_DIR)) fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
if (!fs.existsSync(BACKUP_ROOT)) fs.mkdirSync(BACKUP_ROOT, { recursive: true });

// 加载环境变量
function loadEnvFile() {
  const envPath = join(PROJECT_ROOT, ".env");
  if (fs.existsSync(envPath)) {
    const content = fs.readFileSync(envPath, "utf8");
    for (const line of content.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const idx = trimmed.indexOf("=");
      if (idx > 0) {
        const key = trimmed.slice(0, idx).trim();
        const value = trimmed.slice(idx + 1).trim();
        if (!(key in process.env)) process.env[key] = value;
      }
    }
  }
}

loadEnvFile();

// ==================== 数据库 ====================
// 复用项目既有迁移器（engine/db-migration.mjs）：表已存在则补列，不存在则建表
const db = new Database(join(DATA_DIR, "chat.db"), { verbose: null });
db.pragma("journal_mode = WAL");
db.pragma("synchronous = NORMAL");
db.pragma("busy_timeout = 5000");

runDatabaseMigrations(db);

// FTS5 全文索引（faq_fts 由迁移器创建；此处仅做健康检查）
let faqFtsAvailable = false;
try {
  db.prepare("SELECT rowid FROM faq_fts LIMIT 1").get();
  faqFtsAvailable = true;
} catch {
  console.warn("[DB] FTS5 索引不可用，FAQ 检索回退朴素匹配");
}

// 应用设置表（迁移器可能未覆盖）
db.exec("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)");


// ==================== 工具函数 ====================
function nowIso() { return new Date().toISOString(); }
function safeJson(value, fallback) {
  try { const parsed = JSON.parse(value); return parsed === null || parsed === undefined ? fallback : parsed; } catch { return fallback; }
}
function maskSecret(secret) {
  const s = String(secret || "");
  if (s.length <= 8) return "****";
  return s.slice(0, 4) + "*".repeat(s.length - 8) + s.slice(-4);
}
function envFlag(key, fallback = "1") {
  return String(process.env[key] || fallback) === "1";
}
function getWorkspaceRoot() { return WORKSPACE_ROOT; }
function getScreenshotDir() { return SCREENSHOT_DIR; }

/** 给模型列表补上能力标签（前端用于筛选与展示） */
function withCapabilities(list) {
  return (list || []).map((m) => {
    const modelId = m.modelId || m.name || "";
    const { caps, labels } = classifyModel(modelId);
    return { ...m, capabilities: caps, capabilityLabels: labels };
  });
}

// 可被 @ 引用 / 索引的文本扩展名
const REF_TEXT_EXTS = new Set([
  ".md", ".txt", ".json", ".jsonl", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
  ".py", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".csv", ".tsv", ".html",
  ".htm", ".css", ".scss", ".xml", ".sql", ".sh", ".ps1", ".bat", ".log", ".env"
]);
function getGroupFilesDir(sessionId) { return join(WORKSPACE_ROOT, "group_files", sessionId); }
function mapGroupFile(row, sessionId) {
  return { ...row, url: `/api/sessions/${sessionId}/files/${row.id}/download` };
}
function getDefaultModel() {
  return String(process.env.DEFAULT_MODEL || process.env.OLLAMA_MODEL || "qwen2.5:7b");
}
function getModelRoute(type) {
  const routes = JSON.parse(process.env.MODEL_ROUTES || '{}');
  return routes[type] || "";
}
/** 读取模型分工路由表（默认 / 快速 / 翻译 / 视觉 / 编码） */
function getModelRoutes() {
  let parsed = {};
  try { parsed = JSON.parse(process.env.MODEL_ROUTES || "{}"); } catch { parsed = {}; }
  const defaults = {
    default: getDefaultModel(),
    quick: getDefaultModel(),
    translation: getDefaultModel(),
    vision: getDefaultModel(),
    coding: getDefaultModel()
  };
  return { ...defaults, ...parsed };
}

const INTELLIGENCE_LEVELS = [1, 2, 3, 4, 5, 6].map((level) => ({
  level,
  name: `L${level}`,
  label: ["", "初识", "感知", "理解", "洞悉", "通明", "超然"][level] || ""
}));

let appIntelligenceLevel = Number(process.env.AI_INTELLIGENCE_LEVEL) || 3;

// 模型目录缓存与默认模型
let cachedModels = [];

/** 读取模型目录缓存 */
function getCachedModels() { return cachedModels; }

/** 写入模型目录缓存（handler 刷新时调用） */
function setCachedModels(list) { cachedModels = Array.isArray(list) ? list : []; }
const defaultModel = process.env.DEFAULT_MODEL || getDefaultModel();

function saveModelRoutes(routes) {
  const current = JSON.parse(process.env.MODEL_ROUTES || '{}');
  const merged = { ...current, ...routes };
  process.env.MODEL_ROUTES = JSON.stringify(merged);
  return merged;
}
function getUsagePricing() {
  return {
    input: Number(process.env.USAGE_INPUT_PRICE) || 0,
    output: Number(process.env.USAGE_OUTPUT_PRICE) || 0,
    cached: Number(process.env.USAGE_CACHED_PRICE) || 0,
    currency: String(process.env.USAGE_CURRENCY || "CNY").toUpperCase()
  };
}
function generateGroupNo() {
  const prefix = "AI";
  const timestamp = Date.now().toString(36).toUpperCase();
  const random = Math.random().toString(36).slice(2, 6).toUpperCase();
  return `${prefix}-${timestamp}-${random}`;
}

/** 计算群内下一个成员排序号（sort_order） */
function nextSortOrder(sessionId) {
  const row = db.prepare("SELECT COALESCE(MAX(sort_order), -1) AS m FROM session_agents WHERE session_id = ?").get(sessionId);
  return (row?.m ?? -1) + 1;
}

// Session helpers
function getSession(sessionId) {
  return db.prepare("SELECT * FROM sessions WHERE id = ?").get(sessionId);
}
function createSession(session) {
  const now = nowIso();
  const fullSession = {
    ...session,
    created_at: session.created_at || now,
    updated_at: session.updated_at || now
  };
  db.prepare(`
    INSERT INTO sessions (id, title, model, sdk_session_id, avatar, category, tags, intro, location, announcement,
      my_nickname, remark, pinned, muted, folded, saved, group_no, member_limit, join_policy, speech_mode, mode, target_agent_id, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    fullSession.id, fullSession.title, fullSession.model, fullSession.sdk_session_id,
    fullSession.avatar || "👥", fullSession.category || "家庭",
    JSON.stringify(Array.isArray(fullSession.tags) ? fullSession.tags : []),
    fullSession.intro || "", fullSession.location || "", fullSession.announcement || "",
    fullSession.my_nickname || "", fullSession.remark || "", 0, 0, 0, 1,
    fullSession.group_no || generateGroupNo(), Number(fullSession.member_limit) || 20,
    fullSession.join_policy || "manual", fullSession.speech_mode || "standard",
    fullSession.mode || "group", fullSession.target_agent_id || null,
    fullSession.created_at, fullSession.updated_at
  );
  return fullSession;
}
function updateSession(sessionId, updates) {
  const session = getSession(sessionId);
  if (!session) return false;
  const allowed = ["title", "model", "avatar", "category", "tags", "intro", "location", "announcement",
    "my_nickname", "remark", "pinned", "muted", "folded", "saved", "member_limit", "join_policy",
    "speech_mode", "mode", "target_agent_id"];
  const sets = [];
  const vals = [];
  for (const key of allowed) {
    if (updates[key] !== undefined) {
      sets.push(`${key} = ?`);
      vals.push(updates[key]);
    }
  }
  if (sets.length === 0) return true;
  vals.push(nowIso(), sessionId);
  db.prepare(`UPDATE sessions SET ${sets.join(", ")}, updated_at = ? WHERE id = ?`).run(...vals);
  return true;
}
function getMessagesBySession(sessionId) {
  return db.prepare("SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC").all(sessionId);
}
function createMessage(msg) {
  const now = nowIso();
  db.prepare(`
    INSERT INTO messages (id, session_id, role, content, model, agent_id, agent_name, agent_color, tool_calls, reply_to_id, reply_source, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(msg.id, msg.session_id, msg.role, msg.content, msg.model, msg.agent_id, msg.agent_name, msg.agent_color,
    msg.tool_calls ? JSON.stringify(msg.tool_calls) : null, msg.reply_to_id, msg.reply_source, msg.created_at || now);
}

// Session members
function ensureSessionMembers(sessionId, memberIds) {
  if (!memberIds || !Array.isArray(memberIds) || memberIds.length === 0) return;
  const existing = db.prepare("SELECT agent_id FROM session_agents WHERE session_id = ?").all(sessionId).map(r => r.agent_id);
  const now = nowIso();
  for (const agentId of memberIds) {
    if (!existing.includes(agentId)) {
      db.prepare("INSERT INTO session_agents (session_id, agent_id, enabled, permissions, joined_at, sort_order) VALUES (?, ?, 1, ?, ?, ?)").run(sessionId, agentId, JSON.stringify(["read", "speak"]), now, nextSortOrder(sessionId));
    }
  }
}
function initSessionMembers(sessionId, memberIds) { ensureSessionMembers(sessionId, memberIds); }
function getSessionMemberRecords(sessionId) {
  return db.prepare(`
    SELECT sa.session_id, sa.agent_id, sa.enabled, sa.permissions, sa.sort_order, sa.joined_at,
           a.name, a.role, a.avatar, a.color, a.system_prompt, a.keywords, a.intents
    FROM session_agents sa JOIN agents a ON a.id = sa.agent_id
    WHERE sa.session_id = ? ORDER BY sa.sort_order, sa.joined_at
  `).all(sessionId);
}
function getActiveSessionMembers(sessionId) {
  return getSessionMemberRecords(sessionId).filter(m => m.enabled);
}
function addSessionMember(sessionId, agentId) {
  const session = getSession(sessionId);
  if (!session) return { error: "会话不存在" };
  const agent = db.prepare("SELECT * FROM agents WHERE id = ?").get(agentId);
  if (!agent) return { error: "智能体不存在" };
  const count = db.prepare("SELECT COUNT(*) AS c FROM session_agents WHERE session_id = ? AND enabled = 1").get(sessionId).c;
  if (count >= (Number(session.member_limit) || 20)) return { error: "群成员已达上限" };
  const exists = db.prepare("SELECT * FROM session_agents WHERE session_id = ? AND agent_id = ?").get(sessionId, agentId);
  if (exists) return { error: "该智能体已在群内" };
  const now = nowIso();
  db.prepare("INSERT INTO session_agents (session_id, agent_id, enabled, permissions, joined_at, sort_order) VALUES (?, ?, 1, ?, ?, ?)").run(sessionId, agentId, JSON.stringify(["read", "speak"]), now, nextSortOrder(sessionId));
  return { success: true };
}
function updateSessionMember(sessionId, agentId, updates) {
  const row = db.prepare("SELECT * FROM session_agents WHERE session_id = ? AND agent_id = ?").get(sessionId, agentId);
  if (!row) return { error: "该成员不在群内" };
  const allowed = ["enabled", "permissions", "sort_order"];
  const sets = [];
  const vals = [];
  for (const key of allowed) {
    if (updates[key] !== undefined) {
      sets.push(`${key} = ?`);
      vals.push(updates[key]);
    }
  }
  if (sets.length === 0) return { success: true };
  vals.push(sessionId, agentId);
  db.prepare(`UPDATE session_agents SET ${sets.join(", ")} WHERE session_id = ? AND agent_id = ?`).run(...vals);
  return { success: true };
}
function removeSessionMember(sessionId, agentId) {
  const r = db.prepare("DELETE FROM session_agents WHERE session_id = ? AND agent_id = ?").run(sessionId, agentId);
  return { success: r.changes > 0 };
}

// Agents
function listAgents(enabledOnly = true) {
  const sql = enabledOnly ? "SELECT * FROM agents WHERE enabled = 1 ORDER BY sort, created_at" : "SELECT * FROM agents ORDER BY sort, created_at";
  return db.prepare(sql).all();
}
function getEnabledAgents() { return listAgents(true); }
function createAgent(data) {
  const now = nowIso();
  const id = `agent-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  db.prepare(`
    INSERT INTO agents (id, name, role, avatar, color, system_prompt, keywords, intents, enabled, builtin, sort, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
  `).run(id, data.name, data.role || "", data.avatar || "🤖", data.color || "#0052d9",
    data.systemPrompt || `你是 ${data.name}，${data.role || "AI 助手"}。请用中文简洁回复。`,
    JSON.stringify(data.keywords || []), JSON.stringify(data.intents || []),
    data.enabled !== false ? 1 : 0, data.sort || 99, now, now);
  return db.prepare("SELECT * FROM agents WHERE id = ?").get(id);
}
function updateAgent(id, data) {
  const allowed = ["name", "role", "avatar", "color", "system_prompt", "keywords", "intents", "enabled", "sort"];
  const sets = [];
  const vals = [];
  for (const key of allowed) {
    if (data[key] !== undefined) {
      sets.push(`${key} = ?`);
      vals.push(Array.isArray(data[key]) ? JSON.stringify(data[key]) : data[key]);
    }
  }
  if (sets.length === 0) return true;
  vals.push(nowIso(), id);
  const r = db.prepare(`UPDATE agents SET ${sets.join(", ")}, updated_at = ? WHERE id = ?`).run(...vals);
  return r.changes > 0;
}
function deleteAgent(id) {
  const r = db.prepare("DELETE FROM agents WHERE id = ? AND builtin = 0").run(id);
  return r.changes > 0;
}
function addAgent(data) { return createAgent(data); }
function modifyAgent(id, data) { return updateAgent(id, data); }
function removeAgent(id) { return deleteAgent(id); }

// FAQ
function ensureSeedFaq() {
  const count = db.prepare("SELECT COUNT(*) AS c FROM faq").get()?.c || 0;
  if (count > 0) return;
  const seed = [
    { question: "如何创建新会话？", answer: "在侧边栏点击“新建对话”，或发送消息时不指定 sessionId 即可自动创建。", category: "基础用法" },
    { question: "如何添加 AI 成员到群聊？", answer: "打开会话设置，点击“添加成员”，选择要加入的 AI 角色。", category: "基础用法" },
    { question: "如何使用工具？", answer: "确保在设置中开启了文件工具、终端、GUI 等开关，AI 会在需要时自动调用。", category: "工具调用" }
  ];
  const insert = db.prepare("INSERT INTO faq (id, question, answer, category, keywords, enabled, hits, created_at, updated_at) VALUES (?, ?, ?, ?, '[]', 1, 0, ?, ?)");
  const now = nowIso();
  for (const item of seed) {
    insert.run(`faq-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, item.question, item.answer, item.category, now, now);
  }
}
function ensureFaqFts() {
  if (!faqFtsAvailable) return;
  try {
    db.exec("INSERT INTO faq_fts(faq_fts) VALUES('rebuild')");
  } catch { /* 忽略 */ }
}
function listFaq() { return db.prepare("SELECT * FROM faq ORDER BY hits DESC, updated_at DESC").all(); }
function getAllFaq() { return db.prepare("SELECT * FROM faq").all(); }
function getEnabledFaq() { return db.prepare("SELECT * FROM faq WHERE enabled = 1").all(); }
function addFaq(data) {
  const now = nowIso();
  const id = `faq-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  db.prepare("INSERT INTO faq (id, question, answer, category, keywords, enabled, hits, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)")
    .run(id, data.question, data.answer, data.category || "通用", JSON.stringify(data.keywords || []), data.enabled !== false ? 1 : 0, now, now);
  return { ...data, id, hits: 0, created_at: now, updated_at: now };
}
function modifyFaq(id, data) {
  const allowed = ["question", "answer", "category", "keywords", "enabled"];
  const sets = [];
  const vals = [];
  for (const key of allowed) {
    if (data[key] !== undefined) {
      sets.push(`${key} = ?`);
      vals.push(Array.isArray(data[key]) ? JSON.stringify(data[key]) : data[key]);
    }
  }
  if (sets.length === 0) return true;
  vals.push(nowIso(), id);
  const r = db.prepare(`UPDATE faq SET ${sets.join(", ")}, updated_at = ? WHERE id = ?`).run(...vals);
  return r.changes > 0;
}
function removeFaq(id) {
  const r = db.prepare("DELETE FROM faq WHERE id = ?").run(id);
  return r.changes > 0;
}
function increaseFaqHits(id) { db.prepare("UPDATE faq SET hits = hits + 1 WHERE id = ?").run(id); }
function searchFaq(query, limit = 5) {
  if (!query) return [];
  if (faqFtsAvailable) {
    try {
      return db.prepare("SELECT f.*, bm25(faq_fts) AS rank FROM faq_fts f JOIN faq ON faq.id = f.rowid WHERE faq_fts MATCH ? AND faq.enabled = 1 ORDER BY rank LIMIT ?").all(query, limit);
    } catch { /* fallback */ }
  }
  const q = `%${query}%`;
  return db.prepare("SELECT * FROM faq WHERE enabled = 1 AND (question LIKE ? OR answer LIKE ?) ORDER BY hits DESC LIMIT ?").all(q, q, limit);
}
function markFaqHit(question) {
  const row = db.prepare("SELECT id FROM faq WHERE question = ?").get(question);
  if (row) increaseFaqHits(row.id);
}
function rebuildFaqFts() { ensureFaqFts(); }

// Intent
const INTENT_DEFS = [
  { id: "chat", name: "闲聊", description: "日常对话、问候、情感交流", keywords: ["你好", "嗨", "最近", "心情"], agents: ["陪聊师", "倾听者"] },
  { id: "qa", name: "问答", description: "事实性提问、知识查询", keywords: ["是什么", "怎么", "为什么", "如何", "哪里"], agents: ["知识官", "分析师"] },
  { id: "coding", name: "编程", description: "写代码、调试、重构、架构设计", keywords: ["代码", "函数", "bug", "报错", "重构", "实现"], agents: ["编程伙伴", "架构师"] },
  { id: "writing", name: "写作", description: "文章创作、润色、扩写、摘要", keywords: ["写", "润色", "扩写", "摘要", "文章", "文案"], agents: ["文案师", "润色官"] },
  { id: "analysis", name: "分析", description: "数据分析、趋势研判、逻辑推演", keywords: ["分析", "趋势", "对比", "推演", "统计"], agents: ["分析师", "策略官"] },
  { id: "planning", name: "规划", description: "任务拆解、路线图、目标设定", keywords: ["规划", "计划", "拆解", "路线图", "目标"], agents: ["规划师", "统筹官"] },
  { id: "translation", name: "翻译", description: "多语言互译、本地化", keywords: ["翻译", "英语", "日语", "本地化"], agents: ["翻译官"] },
  { id: "creative", name: "创意", description: "脑暴、点子、设计灵感", keywords: ["创意", "脑暴", "点子", "灵感", "设计"], agents: ["创意总监", "灵感官"] },
  { id: "debug", name: "调试", description: "错误定位、日志分析、复现步骤", keywords: ["调试", "报错", "堆栈", "日志", "复现"], agents: ["调试员", "编程伙伴"] },
  { id: "research", name: "调研", description: "资料搜集、对比评测、技术选型", keywords: ["调研", "对比", "选型", "评测", "资料"], agents: ["调研员", "分析师"] }
];
function getIntentDef(id) { return INTENT_DEFS.find(d => d.id === id); }
function tokenize(text) { return String(text).toLowerCase().match(/[\u4e00-\u9fa5]+|[a-z0-9]+/g) || []; }
function recognizeLocal(text, agents) {
  const tokens = tokenize(text);
  let best = { intent: "chat", confidence: 0.3, matchedAgents: [] };
  for (const def of INTENT_DEFS) {
    let score = 0;
    for (const kw of def.keywords) {
      if (text.includes(kw)) score += 1;
    }
    for (const token of tokens) {
      if (def.keywords.some(kw => kw.includes(token))) score += 0.5;
    }
    const matched = agents.filter(a => def.agents.includes(a.role) || def.agents.includes(a.name));
    if (score > best.confidence * 10) {
      best = { intent: def.id, confidence: Math.min(0.9, score / 10), matchedAgents: matched };
    }
  }
  return best;
}
async function recognizeByApi(text, model, agents) {
  const cfg = getActiveProviderConfig();
  if (!cfg.baseUrl || !cfg.apiKey) return null;
  const prompt = `请判断用户意图，仅输出 JSON：{"intent":"<id>","confidence":0-1,"reason":"..."}。可选意图：${INTENT_DEFS.map(d => d.id).join(",")}。用户："${text}"`;
  try {
    const result = await runAgentTurnLoop({
      provider: cfg.protocol === "claude" ? "claude" : "openai",
      providerConfig: { baseUrl: cfg.baseUrl, apiKey: cfg.apiKey },
      model: model || cfg.model,
      systemPrompt: "你是意图识别器，只输出 JSON。",
      userPrompt: prompt,
      history: [],
      images: [],
      context: {},
      maxTurns: 1,
      temperature: 0.1,
      disableTools: true,
      onText: () => {},
      onToolStart: () => {},
      onToolResult: () => {}
    });
    const parsed = JSON.parse(result.content);
    return { intent: parsed.intent, confidence: parsed.confidence, matchedAgents: agents.filter(a => INTENT_DEFS.find(d => d.id === parsed.intent)?.agents.includes(a.role) || INTENT_DEFS.find(d => d.id === parsed.intent)?.agents.includes(a.name)) };
  } catch { return null; }
}
async function recognizeByOllama(text, model, agents) {
  try {
    return await recognizeByApi(text, model || process.env.OLLAMA_MODEL || "qwen2.5:7b", agents);
  } catch { return null; }
}
async function recognizeIntent(text, { mode = "auto", model, agents }) {
  const agentList = agents || getEnabledAgents().map(a => ({ id: a.id, name: a.name, role: a.role }));
  if (mode === "local" || mode === "auto") {
    const local = recognizeLocal(text, agentList);
    if (mode === "local" || local.confidence >= 0.6) return local;
  }
  if (mode === "api" || mode === "auto") {
    const provider = getAiProvider();
    if (provider === "ollama") {
      const ollama = await recognizeByOllama(text, model, agentList);
      if (ollama) return ollama;
    } else if (provider === "direct") {
      const api = await recognizeByApi(text, model, agentList);
      if (api) return api;
    }
  }
  return recognizeLocal(text, agentList);
}
function getIntentTimeoutMs() { return Number(process.env.INTENT_TIMEOUT_MS) || 30000; }
function getMaxModelAttempts() { return Number(process.env.MAX_MODEL_ATTEMPTS) || 1; }
function clampMaxAgents(n) { return Math.min(Math.max(Number(n) || 3, 1), 8); }

// Provider
function getAiProvider() { return String(process.env.AI_PROVIDER || "ollama").toLowerCase(); }
function isOllamaProvider() { return getAiProvider() === "ollama"; }
function isDirectProvider() { return getAiProvider() === "direct"; }
function getActiveProviderConfig() {
  const provider = getAiProvider();
  if (provider === "direct") {
    const preset = JSON.parse(process.env.DIRECT_PROVIDER_PRESET || '{}');
    return { key: preset.key || "direct", baseUrl: preset.baseUrl, apiKey: preset.apiKey, model: preset.model, protocol: preset.protocol || "openai" };
  }
  return null;
}
function normalizeOllamaBaseUrl() {
  let u = String(process.env.OLLAMA_BASE_URL || "http://localhost:11434").trim();
  if (!/^https?:\/\//i.test(u)) u = `http://${u}`;
  return u.replace(/\/+$/, "");
}
function getOllamaDefaultModel() { return String(process.env.OLLAMA_MODEL || "qwen2.5:7b"); }
function getOllamaTimeoutMs() { return Number(process.env.OLLAMA_TIMEOUT_MS) || 120000; }
function getOllamaStreamIdleMs() { return Number(process.env.OLLAMA_STREAM_IDLE_MS) || 30000; }
function getOllamaModelsTimeoutMs() { return Number(process.env.OLLAMA_MODELS_TIMEOUT_MS) || 10000; }
function getOllamaOptions() { return JSON.parse(process.env.OLLAMA_OPTIONS || '{"temperature":0.7,"num_ctx":8192}'); }
async function fetchOllama(path, options = {}) {
  const base = normalizeOllamaBaseUrl();
  const res = await fetch(`${base}${path}`, { ...options, headers: { "Content-Type": "application/json", ...options.headers }, signal: AbortSignal.timeout(getOllamaTimeoutMs()) });
  if (!res.ok) throw new Error(`Ollama ${res.status}: ${await res.text().catch(()=>"")}`);
  return res.json();
}
async function openOllama() {
  try { await fetchOllama("/api/tags"); return true; } catch { return false; }
}
async function getOllamaModels() {
  const data = await fetchOllama("/api/tags");
  return (data.models || []).map(m => ({ modelId: m.name, name: m.name, size: m.size }));
}
async function ollamaChatOnce(messages, model, options = {}) {
  const payload = { model: model || getOllamaDefaultModel(), messages, stream: false, options: { ...getOllamaOptions(), ...options } };
  const data = await fetchOllama("/api/chat", { method: "POST", body: JSON.stringify(payload) });
  return data.message?.content || "";
}
function parseOllamaStreamLine(line) {
  try { return JSON.parse(line); } catch { return null; }
}
async function* ollamaChatStream(messages, model, options = {}) {
  const payload = { model: model || getOllamaDefaultModel(), messages, stream: true, options: { ...getOllamaOptions(), ...options } };
  const res = await fetch(`${normalizeOllamaBaseUrl()}/api/chat`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const lines = buf.split("\n");
    buf = lines.pop() || "";
    for (const line of lines) {
      const parsed = parseOllamaStreamLine(line);
      if (parsed?.message?.content) yield { type: "text", content: parsed.message.content };
      if (parsed?.done) return;
    }
  }
}

// Settings
function writeEnvVars(vars) {
  const envPath = join(PROJECT_ROOT, ".env");
  let content = fs.existsSync(envPath) ? fs.readFileSync(envPath, "utf8") : "";
  const lines = content.split("\n");
  const existing = new Map();
  for (const line of lines) {
    const idx = line.indexOf("=");
    if (idx > 0) existing.set(line.slice(0, idx).trim(), line);
  }
  for (const [key, value] of Object.entries(vars)) {
    existing.set(key, `${key}=${value}`);
  }
  fs.writeFileSync(envPath, Array.from(existing.values()).join("\n") + "\n", "utf8");
  for (const [key, value] of Object.entries(vars)) process.env[key] = value;
}
function ensureAdminPassword() {
  if (!process.env.ADMIN_PASSWORD) {
    const pwd = crypto.randomBytes(16).toString("hex");
    writeEnvVars({ ADMIN_PASSWORD: pwd });
    console.log(`[Admin] 已生成随机管理密码: ${pwd}`);
    return pwd;
  }
  return process.env.ADMIN_PASSWORD;
}
function buildAdminLoginPage() {
  return `<!DOCTYPE html><html><head><meta charset="utf-8"><title>管理登录</title><style>body{font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#f5f5f5}.card{background:#fff;padding:2rem;border-radius:12px;box-shadow:0 4px 20px rgba(0,0,0,.1);width:100%;max-width:360px}input{width:100%;padding:.75rem;margin:.5rem 0;border:1px solid #ddd;border-radius:8px;box-sizing:border-box}button{width:100%;padding:.75rem;background:#0052d9;color:#fff;border:none;border-radius:8px;font-size:1rem;cursor:pointer}button:hover{background:#003eb8}</style></head><body><div class="card"><h2>AI 神殿 · 管理后台</h2><form id="f"><input type="password" name="password" placeholder="管理密码" required autofocus><button type="submit">登录</button></form><p style="color:#666;font-size:.85rem;margin-top:1rem">密码见 .env 的 ADMIN_PASSWORD</p><script>document.getElementById('f').onsubmit=async e=>{e.preventDefault();const r=await fetch('/api/admin/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:new FormData(e.target).get('password')})});const j=await r.json();if(j.success)location.href='/admin';else alert(j.error||'登录失败')}</script></div></body></html>`;
}

// App settings (存储在数据库)
function getAppSetting(key, fallback = "") {
  try {
    const row = db.prepare("SELECT value FROM app_settings WHERE key = ?").get(key);
    return row ? row.value : fallback;
  } catch { return fallback; }
}
function setAppSetting(key, value) {
  db.prepare("INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)").run(key, value, nowIso());
}
db.exec("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)");

// ==================== 中间件依赖注入（切断循环依赖） ====================
setOracleSettingStore({ getAppSetting, setAppSetting });
setGuardSettingStore({ getAppSetting, setAppSetting });
setActiveTaskCounter(() =>
  db.prepare("SELECT COUNT(*) AS c FROM autonomous_tasks WHERE status = 'running'").get()?.c || 0
);


// Open API
function getOpenApiConfig() {
  const enabled = getAppSetting("open_api_enabled", "0") === "1";
  const webhookEnabled = getAppSetting("webhook_enabled", "0") === "1";
  const webhookUrl = getAppSetting("webhook_url", "");
  const keys = getOpenApiKeys();
  return { enabled, keys, webhookUrl, webhookEnabled };
}
function getOpenApiKeys() {
  try { return JSON.parse(getAppSetting("open_api_keys", "[]")); } catch { return []; }
}
function setOpenApiKeys(keys) { setAppSetting("open_api_keys", JSON.stringify(keys)); }
function findApiKey(provided) {
  const keys = getOpenApiKeys();
  for (const k of keys) {
    if (crypto.timingSafeEqual(Buffer.from(k.key), Buffer.from(provided))) return k;
  }
  return null;
}
function hasPermission(keyObj, req) {
  const perms = keyObj.permissions || ["read"];
  const path = req.path;
  if (path.startsWith("/api/open/chat") && perms.includes("chat")) return true;
  if (path.startsWith("/api/open/tasks") && perms.includes("tasks")) return true;
  if (perms.includes("admin")) return true;
  return false;
}
function ensureOpenApiKey() {
  const keys = getOpenApiKeys();
  if (keys.length === 0) {
    const newKey = {
      id: `key-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      name: "Default Key",
      key: crypto.randomBytes(24).toString("hex"),
      permissions: ["read", "chat", "tasks"],
      createdAt: nowIso(),
      lastUsed: null
    };
    keys.push(newKey);
    setOpenApiKeys(keys);
  }
}

// Notification & Webhook
function createNotification(data) {
  const id = `notif-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  db.prepare("INSERT INTO notifications (id, type, title, body, link, read, created_at) VALUES (?, ?, ?, ?, ?, 0, ?)")
    .run(id, data.type, data.title, data.body || "", data.link || "", nowIso());
}
async function sendWebhook(event, payload) {
  const cfg = getOpenApiConfig();
  if (!cfg.webhookEnabled || !cfg.webhookUrl) return false;
  try {
    await fetch(cfg.webhookUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ event, payload, timestamp: nowIso() }),
      signal: AbortSignal.timeout(5000)
    });
    return true;
  } catch { return false; }
}

// Admin stats
function getAdminStats() {
  return {
    sessions: db.prepare("SELECT COUNT(*) AS c FROM sessions").get().c,
    messages: db.prepare("SELECT COUNT(*) AS c FROM messages").get().c,
    agents: db.prepare("SELECT COUNT(*) AS c FROM agents WHERE enabled = 1").get().c,
    faq: db.prepare("SELECT COUNT(*) AS c FROM faq WHERE enabled = 1").get().c,
    autonomousTasks: db.prepare("SELECT COUNT(*) AS c FROM autonomous_tasks").get().c,
    goals: db.prepare("SELECT COUNT(*) AS c FROM agent_goals WHERE status = 'active'").get().c,
    notes: db.prepare("SELECT COUNT(*) AS c FROM notes").get().c,
    codingRuns: db.prepare("SELECT COUNT(*) AS c FROM coding_runs").get().c
  };
}
function getAdminSessions() {
  return db.prepare("SELECT id, title, model, mode, created_at, updated_at, (SELECT COUNT(*) FROM messages WHERE session_id = sessions.id) AS msgCount FROM sessions ORDER BY updated_at DESC LIMIT 50").all();
}
function deleteSession(id) {
  const r = db.prepare("DELETE FROM sessions WHERE id = ?").run(id);
  if (r.changes > 0) {
    db.prepare("DELETE FROM messages WHERE session_id = ?").run(id);
    db.prepare("DELETE FROM session_agents WHERE session_id = ?").run(id);
    db.prepare("DELETE FROM group_files WHERE session_id = ?").run(id);
  }
  return r.changes > 0;
}
function searchMessages({ sessionId, keyword, role, intent, source, limit = 100, offset = 0 }) {
  const clauses = [];
  const params = [];
  if (sessionId) { clauses.push("session_id = ?"); params.push(sessionId); }
  if (keyword) { clauses.push("content LIKE ?"); params.push(`%${keyword}%`); }
  if (role) { clauses.push("role = ?"); params.push(role); }
  if (intent) { clauses.push("reply_source = ?"); params.push(intent); }
  if (source) { clauses.push("reply_source = ?"); params.push(source); }
  const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
  params.push(limit, offset);
  return db.prepare(`SELECT * FROM messages ${where} ORDER BY created_at DESC LIMIT ? OFFSET ?`).all(...params);
}
function getIntentLogs(limit = 100) {
  return db.prepare("SELECT * FROM autonomous_tasks ORDER BY created_at DESC LIMIT ?").all(limit);
}
function countIntentsBySource() {
  return db.prepare("SELECT reply_source AS source, COUNT(*) AS count FROM messages WHERE reply_source IS NOT NULL GROUP BY reply_source").all();
}
function countIntentsByType() {
  return db.prepare("SELECT reply_source AS type, COUNT(*) AS count FROM messages WHERE reply_source IS NOT NULL GROUP BY reply_source").all();
}
function mapNotification(row) {
  return { ...row, read: Boolean(row.read) };
}
function mapAutonomousTask(row) {
  return { ...row, allowWrites: Boolean(row.allow_writes) };
}
function computeNextRun(recur, from = new Date()) {
  const next = new Date(from);
  if (recur.recurrence === "daily") next.setDate(next.getDate() + 1);
  else if (recur.recurrence === "weekly") next.setDate(next.getDate() + (7 - next.getDay() + recur.recur_weekday) % 7 || 7);
  else if (recur.recurrence === "monthly") next.setMonth(next.getMonth() + 1, recur.recur_monthday);
  else return null;
  const [h, m] = recur.recur_time.split(":").map(Number);
  next.setHours(h, m, 0, 0);
  if (next <= from) return computeNextRun(recur, next);
  return next.toISOString();
}

// Task Queue
const taskQueue = {
  enqueue(task) {
    const id = `task-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const now = nowIso();
    const full = { ...task, id, status: "pending", attempts: 0, created_at: now, updated_at: now };
    db.prepare(`
      INSERT INTO autonomous_tasks (id, session_id, goal, priority, status, due_at, goal_id, model, allow_writes, recurrence, recur_time, recur_weekday, recur_monthday, max_retry, timeout_sec, attempts, created_at, updated_at, origin)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(full.id, full.sessionId, full.goal, full.priority, full.status, full.dueAt, full.goalId, full.model, full.allowWrites, full.recurrence, full.recurTime, full.recurWeekday, full.recurMonthday, full.maxRetry, full.timeoutSec, full.attempts, full.created_at, full.updated_at, full.origin);
    return db.prepare("SELECT * FROM autonomous_tasks WHERE id = ?").get(id);
  },
  requeue(id) {
    const r = db.prepare("UPDATE autonomous_tasks SET status = 'pending', attempts = 0, last_error = NULL, updated_at = ? WHERE id = ?").run(nowIso(), id);
    return r.changes > 0;
  },
  status() {
    const counts = db.prepare("SELECT status, COUNT(*) AS c FROM autonomous_tasks GROUP BY status").all();
    return Object.fromEntries(counts.map(c => [c.status, c.c]));
  },
  recoverOnStart() {
    const rows = db.prepare("SELECT * FROM autonomous_tasks WHERE status IN ('running', 'pending') AND due_at IS NOT NULL AND due_at <= ?").all(nowIso());
    for (const row of rows) {
      db.prepare("UPDATE autonomous_tasks SET status = 'pending', updated_at = ? WHERE id = ?").run(nowIso(), row.id);
    }
    return rows.length;
  },
  start() { /* 调度逻辑在后台运行 */ },
  stop() { /* 停止调度 */ },
  tick() { /* 处理一个任务 */ }
};

// Proactive Patrol
const proactivePatrol = {
  status() { return { enabled: getAppSetting("patrol_enabled", "1") === "1", intervalMinutes: Number(getAppSetting("patrol_interval", "30")) }; },
  configure({ enabled, intervalMinutes }) {
    if (enabled !== undefined) setAppSetting("patrol_enabled", enabled ? "1" : "0");
    if (intervalMinutes !== undefined) setAppSetting("patrol_interval", String(Math.max(5, Math.min(1440, intervalMinutes))));
    return this.status();
  },
  async runOnce({ trigger }) { return { trigger, checked: 0, actions: 0 }; },
  start() { /* 定时器 */ },
  stop() { /* 清理定时器 */ }
};

// User Profile Service
const userProfileService = {
  get() {
    const row = db.prepare("SELECT * FROM user_profile WHERE id = 1").get();
    return row ? { ...row, topics: safeJson(row.topics, []), intents: safeJson(row.intents, []) } : null;
  },
  rebuild(days = 30) {
    const since = new Date(Date.now() - days * 86400000).toISOString();
    const msgs = db.prepare("SELECT content, role, created_at FROM messages WHERE created_at > ? AND role = 'user' ORDER BY created_at DESC LIMIT 500").all(since);
    const summary = msgs.slice(0, 5).map(m => m.content.slice(0, 100)).join(" | ");
    const topics = [...new Set(msgs.flatMap(m => tokenize(m.content).filter(t => t.length > 1)))].slice(0, 30);
    const intents = [...new Set(msgs.map(m => recognizeLocal(m.content, []).intent))].slice(0, 10);
    const profile = { summary, topics, intents, updatedAt: nowIso() };
    db.prepare("INSERT OR REPLACE INTO user_profile (id, summary, topics, intents, updated_at) VALUES (1, ?, ?, ?, ?)")
      .run(summary, JSON.stringify(topics), JSON.stringify(intents), profile.updatedAt);
    return profile;
  }
};

// Goal Service
const goalService = {
  list(status) {
    let sql = "SELECT * FROM agent_goals";
    const params = [];
    if (status) { sql += " WHERE status = ?"; params.push(status); }
    sql += " ORDER BY priority DESC, created_at DESC";
    return db.prepare(sql).all(...params);
  },
  create({ title, description, acceptance, deadline, sessionId, priority }) {
    const id = `goal-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const now = nowIso();
    db.prepare("INSERT INTO agent_goals (id, title, description, acceptance, deadline, session_id, priority, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)")
      .run(id, title, description || "", acceptance || "", deadline || null, sessionId || null, priority || 0, now, now);
    return db.prepare("SELECT * FROM agent_goals WHERE id = ?").get(id);
  },
  update(id, data) {
    const allowed = ["title", "description", "acceptance", "deadline", "priority", "status", "progress"];
    const sets = []; const vals = [];
    for (const key of allowed) if (data[key] !== undefined) { sets.push(`${key} = ?`); vals.push(data[key]); }
    if (sets.length === 0) return db.prepare("SELECT * FROM agent_goals WHERE id = ?").get(id);
    vals.push(nowIso(), id);
    db.prepare(`UPDATE agent_goals SET ${sets.join(", ")}, updated_at = ? WHERE id = ?`).run(...vals);
    return db.prepare("SELECT * FROM agent_goals WHERE id = ?").get(id);
  },
  remove(id) { return db.prepare("DELETE FROM agent_goals WHERE id = ?").run(id).changes > 0; },
  async review(id) {
    const goal = db.prepare("SELECT * FROM agent_goals WHERE id = ?").get(id);
    if (!goal) throw new Error("目标不存在");
    // 这里可以接入 AI 评估
    return this.update(id, { status: "completed", progress: 100, reviewed_at: nowIso() });
  }
};

// Strategy Library
const strategyLibrary = {
  list(limit = 50) { return db.prepare("SELECT * FROM strategy_library ORDER BY use_count DESC, avg_score DESC LIMIT ?").all(limit); },
  remove(id) { return db.prepare("DELETE FROM strategy_library WHERE id = ?").run(id).changes > 0; },
  evaluateAndPrune(minReuseCount, minSuccessRate, minAvgScore) {
    const rows = db.prepare("SELECT * FROM strategy_library WHERE use_count >= ?").all(minReuseCount);
    let pruned = 0;
    for (const r of rows) {
      const successRate = r.reuse_count > 0 ? (r.reuse_success_count || 0) / r.reuse_count : 0;
      if (successRate < minSuccessRate || (r.avg_score || 0) < minAvgScore) {
        db.prepare("DELETE FROM strategy_library WHERE id = ?").run(r.id);
        pruned++;
      }
    }
    return { evaluated: rows.length, pruned, remaining: rows.length - pruned };
  }
};

// Backup
function autoBackupEnabled() { return getAppSetting("auto_backup", "1") === "1"; }
async function runAutoBackupIfDue() {
  if (!autoBackupEnabled()) return;
  const last = getAppSetting("last_backup", "0");
  if (Date.now() - Number(last) < 6 * 3600e3) return;
  const id = `backup-${Date.now()}`;
  const dest = join(BACKUP_ROOT, `${id}.db`);
  fs.copyFileSync(join(DATA_DIR, "chat.db"), dest);
  setAppSetting("last_backup", String(Date.now()));
  console.log(`[Backup] 自动备份完成: ${dest}`);
}

// SSE
function setupSSE(res) {
  res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", Connection: "keep-alive", "X-Accel-Buffering": "no" });
  res.write("\n");
  const cleanup = () => { try { res.end(); } catch {} };
  req?.on?.("close", cleanup);
  return { send: (data) => res.write(`data: ${JSON.stringify(data)}\n\n`), cleanup };
}
function sendSSE(res, data) { res.write(`data: ${JSON.stringify(data)}\n\n`); }

// Chat modes
async function runGroupChat(res, { sessionId, message, model, intentMode, maxAgents, enableSummary, permissionMode, cwd, images, allowWrites, replyToId }) {
  // 简化：实际实现需要完整的群聊逻辑
  sendSSE(res, { type: "text", content: "[GroupChat] 模式运行中..." });
}
async function runPrivateChat(res, { sessionId, message, model, targetAgentId, permissionMode, cwd, images, allowWrites }) {
  sendSSE(res, { type: "text", content: "[PrivateChat] 模式运行中..." });
}
async function runWorkflow(res, { sessionId, message, model, workflowType, permissionMode, cwd }) {
  sendSSE(res, { type: "text", content: "[Workflow] 模式运行中..." });
}
async function runProactiveService(res, { sessionId, message, model, permissionMode, cwd, images, allowWrites }) {
  sendSSE(res, { type: "text", content: "[Proactive] 模式运行中..." });
}
async function runContinuousDialogue(res, { sessionId, message, model, permissionMode, cwd, images, allowWrites }) {
  sendSSE(res, { type: "text", content: "[Continuous] 模式运行中..." });
}
async function runCreativeCollaboration(res, { sessionId, message, model, permissionMode, cwd, images, allowWrites }) {
  sendSSE(res, { type: "text", content: "[Creative] 模式运行中..." });
}
async function runDataAnalysis(res, { sessionId, message, model, permissionMode, cwd, images, allowWrites }) {
  sendSSE(res, { type: "text", content: "[Analysis] 模式运行中..." });
}
async function runAutonomousAgent(res, { sessionId, message, model, permissionMode, cwd, images, allowWrites, resumeTaskId }) {
  sendSSE(res, { type: "text", content: "[Autonomous] 模式运行中..." });
}
async function runCrossPlatform(res, { sessionId, message, model, permissionMode, cwd, images, allowWrites }) {
  sendSSE(res, { type: "text", content: "[CrossPlatform] 模式运行中..." });
}

// Record token usage
function recordTokenUsage({ sessionId, agentId, mode, model, usage }) {
  // 可以存入数据库用于计费统计
}

// ==================== Express 应用初始化 ====================
const generatedAdminPassword = ensureAdminPassword();
const app = express();
const PORT = Number(process.env.PORT) || DEFAULT_VALUES.PORT;
const BIND_HOST = getBindHost();

app.set("trust proxy", false);
app.disable("x-powered-by");
app.use(express.json({ limit: "10mb" }));
app.use(requestLogger);

// ==================== 家庭访问口令中间件 ====================
app.use("/api", (req, res, next) => {
  if (!getFamilyPassword()) return next();
  const p = req.path;
  if (p === "/api/health" || p.startsWith("/api/family/")) return next();
  if ((p === "/api/desktop/notifications" || p === "/api/desktop/selection" || p === "/api/desktop/selection/ack") && isLoopbackRequest(req)) return next();
  if (p.startsWith("/api/admin/") || p === "/api/check-login" || p === "/api/save-env-config" || p === "/api/config-permission") return next();
  if (isFamilyAuthenticated(req)) return next();
  return res.status(401).json({ error: "需要家庭访问口令", code: "FAMILY_AUTH_REQUIRED" });
});

// ==================== 注册路由 ====================
registerSessionRoutes(app, sessionHandlers);
registerChatRoutes(app, chatHandlers);
registerMemoryRoutes(app, memoryHandlers);
registerAutonomyRoutes(app, autonomyHandlers);
registerPantheonRoutes(app, pantheonHandlers);
registerToolsRoutes(app, toolsHandlers);
registerSettingsRoutes(app, settingsHandlers);
registerOpenRoutes(app, openHandlers);
registerAdminRoutes(app, adminHandlers);
registerDesktopRoutes(app, desktopHandlers);

// Store endpoints
registerStoreEndpoints(app, db);

// ==================== 编码任务运行时 ====================
const codingRuns = new Map();

/** 把一次编码运行的收尾结果写成笔记，便于后续检索 */
function createCodingNote(run) {
  try {
    if (!run || !run.goal) return null;
    const statusLabel = run.status === "completed" ? "完成"
      : run.status === "stopped" ? "已停止" : "失败";
    const body = [
      `目标: ${run.goal}`,
      `代理: ${run.agentName}`,
      `模型: ${run.model || "默认"}`,
      `命令: ${run.command}`,
      `退出码: ${run.exitCode ?? "-"}`,
      "",
      "## 输出",
      "```",
      String(run.output || "").slice(-8000),
      "```"
    ].join("\n");
    const note = notesService.create({
      title: `编码${statusLabel}: ${run.goal.slice(0, 60)}`,
      content_md: body,
      tags: ["coding", run.agentKey],
      owner: "agent",
      visibility: "shared",
      pinned: false
    });
    return note;
  } catch (e) {
    console.warn("[Coding] 生成笔记失败(忽略):", e?.message || e);
    return null;
  }
}

/** 向所有订阅该运行的 SSE 监听器推送事件 */
function broadcastCoding(run, payload) {
  if (!run || !run.listeners) return;
  const frame = `data: ${JSON.stringify(payload)}\n\n`;
  for (const res of run.listeners) {
    try { res.write(frame); } catch { /* 监听器已断开 */ }
  }
}

// ==================== 笔记 / 工作日志服务 ====================
const notesService = createNotesService(db);
const journalService = createJournalService(db);
const notesApi = notesToolApi(notesService);
bindNotesService(notesService);
bindJournalService(journalService);


// 全局错误处理中间件 (必须在路由之后)
app.use(errorHandler);
app.use(notFoundHandler);


// ==================== 启动服务 ====================
// ---- 开放 API 依赖注入（auth.mjs 的 requireOpenApiKey 需要） ----
setOpenApiDeps({
  getOpenApiConfig,
  getOpenApiKeys,
  setOpenApiKeys,
  findApiKey,
  hasPermission,
  sendWebhook
});

const server = app.listen(PORT, BIND_HOST, () => {
  const shownHost = isLoopbackHost(BIND_HOST) ? "localhost" : BIND_HOST;
  const net = isLoopbackHost(BIND_HOST) ? "仅本机" : "对外开放";

  console.log("");
  console.log("┌────────────────────────────────────────────────────────────────┐");
  console.log(`      ${APP_NAME} · 服务已启动 (模块化 v${APP_VERSION})`);
  console.log(`      地址: http://${shownHost}:${PORT}`);
  console.log(`      绑定: ${BIND_HOST} · ${net}`);
  console.log("      数据库: SQLite (data/chat.db)");
  console.log("      日志: logs/server.log · 追加写入不覆盖");
  console.log("└────────────────────────────────────────────────────────────────┘");
  console.log("");

  if (!isLoopbackHost(BIND_HOST)) {
    console.log(`[Net] 已对外开放，局域网可通过 http://<本机IP>:${PORT} 访问`);
    if (adminAuthRequired()) console.log("[Net] 管理后台 /admin 需要密码登录（见 .env 的 ADMIN_PASSWORD）");
  }

  // 种子数据
  ensureSeedFaq();
  ensureStoreSeed(db);

  setTimeout(() => {
    // MCP 连接（如果有 mcpManager）
    // taskQueue 恢复
    try {
      const recovered = taskQueue.recoverOnStart();
      if (recovered > 0) console.log(`[Queue] 服务重启恢复 ${recovered} 个中断任务，已重新排队`);
      taskQueue.start();
    } catch (e) { console.warn("[Queue] 调度启动失败(忽略):", e?.message || e); }

    try { proactivePatrol.start(); } catch (e) { console.warn("[Patrol] 巡检启动失败(忽略):", e?.message || e); }

    // 资源监控
    startResourceMonitor(30000);
  }, 1200);
});

// ==================== 定时任务 ====================
function checkpointDb() {
  try {
    const r = db.pragma("wal_checkpoint(PASSIVE)");
    if (r && r[0] && r[0].busy !== 0) return;
  } catch (e) { console.warn("[DB] WAL checkpoint 失败(忽略):", e?.message || e); }
}
const checkpointTimer = setInterval(checkpointDb, 6e4);
if (typeof checkpointTimer.unref === "function") checkpointTimer.unref();

const memoryCleanupTimer = setInterval(() => {
  try {
    db.prepare("DELETE FROM context_memory WHERE expires_at IS NOT NULL AND expires_at < ?")
      .run(nowIso());
  } catch (e) { console.warn("[Memory] 记忆清理失败(忽略):", e?.message || e); }
}, 36e5);
if (typeof memoryCleanupTimer.unref === "function") memoryCleanupTimer.unref();

setTimeout(() => {
  runAutoBackupIfDue().catch((e) => console.warn("[Backup] 自动备份失败(忽略):", e?.message || e));
}, 45e3).unref?.();

const backupTimer = setInterval(() => {
  runAutoBackupIfDue().catch((e) => console.warn("[Backup] 自动备份失败(忽略):", e?.message || e));
}, 6 * 3600e3);
if (typeof backupTimer.unref === "function") backupTimer.unref();

// ==================== 优雅退出 ====================
function shutdown(signal) {
  console.log(`[Server] 收到 ${signal}，正在退出...`);
  let finished = false;
  const done = () => {
    if (finished) return;
    finished = true;
    clearInterval(checkpointTimer);
    clearInterval(memoryCleanupTimer);
    clearInterval(backupTimer);
    try { taskQueue.stop(); } catch { }
    try { proactivePatrol.stop(); } catch { }
    try {
      db.pragma("wal_checkpoint(TRUNCATE)");
      console.log("[DB] WAL 已写回 data/chat.db");
    } catch (e) { console.warn("[DB] WAL checkpoint 失败(忽略):", e?.message || e); }
    try { db.close(); } catch { }
    // mcpManager?.disconnectAll?.();
    process.exit(0);
  }
  try {
    server.close(() => done());
    const t = setTimeout(done, 3e3);
    if (typeof t.unref === "function") t.unref();
  } catch { done(); }
}

process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));
process.on("uncaughtException", (err) => console.error("[Server] 未捕获异常(进程继续运行):", err?.stack || err));
process.on("unhandledRejection", (reason) => console.error("[Server] 未处理的 Promise 拒绝(进程继续运行):", reason));

// 导出供其他模块使用
export {
  // ===== Express / DB =====
  app, db, server,

  // ===== 通用工具 =====
  nowIso, safeJson, maskSecret, envFlag,

  // ===== 路径 =====
  getWorkspaceRoot, getScreenshotDir, getGroupFilesDir, mapGroupFile,
  BACKUP_ROOT, WORKSPACE_ROOT, DATA_DIR, DIST_DIR, SCREENSHOT_DIR, PROJECT_ROOT,
  REF_TEXT_EXTS,

  // ===== 模型与提供商 =====
  getDefaultModel, getModelRoute, getModelRoutes, saveModelRoutes, getUsagePricing,
  getCachedModels, setCachedModels,
  defaultModel,
  getAiProvider, isOllamaProvider, isDirectProvider, getActiveProviderConfig,
  normalizeOllamaBaseUrl, getOllamaDefaultModel, getOllamaTimeoutMs,
  getOllamaStreamIdleMs, getOllamaModelsTimeoutMs, getOllamaOptions,
  fetchOllama, openOllama, getOllamaModels, ollamaChatOnce, parseOllamaStreamLine, ollamaChatStream,

  // ===== 会话 =====
  generateGroupNo, getSession, createSession, updateSession, getMessagesBySession, createMessage,
  ensureSessionMembers, initSessionMembers, getSessionMemberRecords, getActiveSessionMembers,
  addSessionMember, updateSessionMember, removeSessionMember,

  // ===== 智能体（handler 内同名 → 统一用 Fn 别名） =====
  listAgents as listAgentsFn,
  getEnabledAgents as getEnabledAgentsFn,
  createAgent as createAgentFn,
  updateAgent as updateAgentFn,
  deleteAgent as deleteAgentFn,
  addAgent as addAgentFn,
  modifyAgent as modifyAgentFn,
  removeAgent as removeAgentFn,

  // ===== FAQ（同上） =====
  ensureSeedFaq, ensureFaqFts, listFaq, getAllFaq, getEnabledFaq,
  addFaq as addFaqFn,
  modifyFaq as modifyFaqFn,
  removeFaq as removeFaqFn,
  increaseFaqHits,
  searchFaq as searchFaqFn,
  markFaqHit, rebuildFaqFts, faqFtsAvailable,

  // ===== 意图识别 =====
  INTENT_DEFS, getIntentDef, tokenize, recognizeLocal, recognizeIntent,
  recognizeByApi, recognizeByOllama,
  getIntentTimeoutMs, getMaxModelAttempts, clampMaxAgents,

  // ===== 应用设置 / 环境 =====
  getAppSetting, setAppSetting, writeEnvVars,
  ensureAdminPassword, buildAdminLoginPage,
  INTELLIGENCE_LEVELS, appIntelligenceLevel,

  // ===== 开放 API =====
  getOpenApiConfig, getOpenApiKeys, setOpenApiKeys, findApiKey,
  hasPermission as hasApiKeyPermission, ensureOpenApiKey,

  // ===== 通知 / Webhook =====
  createNotification, sendWebhook, mapNotification,

  // ===== 管理后台 =====
  getAdminStats, getAdminSessions, deleteSession, searchMessages, getIntentLogs,
  countIntentsBySource, countIntentsByType,

  // ===== 自主性 =====
  mapAutonomousTask, computeNextRun,
  taskQueue, proactivePatrol, userProfileService, goalService, strategyLibrary,

  // ===== 备份 =====
  autoBackupEnabled, runAutoBackupIfDue,

  // ===== SSE 与对话模式 =====
  setupSSE, sendSSE,
  runGroupChat, runPrivateChat, runWorkflow, runProactiveService, runContinuousDialogue,
  runCreativeCollaboration, runDataAnalysis, runAutonomousAgent, runCrossPlatform,
  recordTokenUsage,

  // ===== 引擎 =====
  ensureStoreSeed, getEnabledStorePrompt, registerStoreEndpoints,
  uiaController, callOpenAIStream, callClaudeStream, callOllamaStream, runAgentTurnLoop,
  TOOLS_OPENAI, TOOLS_CLAUDE, executeTool, getEnabledTools, classifyModel, withCapabilities,
  buildCodingCommand, resolveCodingCwd, findCodingAgent, detectCodingAgents,
  getCodingProviderEnv, quoteShellArg, codingRuns, createCodingNote, broadcastCoding,
  createNoteRecord, getNoteRecord, searchNoteRecords, toNote,
  appendJournal, parseMaybeJson, notesToolApi,
  notesService, journalService,

  // ===== 认证原语（转出自 auth-core.mjs） =====
  getFamilyPassword, familySessionToken, parseCookies, isFamilyAuthenticated,
  getBindHost, isLoopbackHost, isLoopbackRequest,
  adminAuthRequired, adminToken, isAdminAuthed, adminCookieHeader,
  isFamilyExemptPath, isDesktopExemptPath, isAdminExemptPath,

  // ===== 常量 =====
  APP_VERSION, APP_NAME, BUILD_DATE, DEFAULT_VALUES, ERROR_CODES, HTTP_STATUS,
  REALM_THRESHOLDS, MEMORY_REALM_LAYERS, TRIAL_CONFIG, TRIAL_XP_BONUS, PERMISSION_ACTIONS,
  FEATURES, isEnabled, enable, disable, getAllFeatures,
  REALMS, PERMISSION_MATRIX, getRealmConfig, getNextTrial, canPromote
};
