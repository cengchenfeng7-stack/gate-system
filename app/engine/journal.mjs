// AI 工作日志（agent_journal）：追加写入、查询

import crypto from "crypto";

function nowIso() { return new Date().toISOString(); }

export function parseMaybeJson(value, fallback = null) {
  if (value === null || value === undefined) return fallback;
  if (typeof value === "object") return value;
  try {
    const parsed = JSON.parse(value);
    return parsed === null || parsed === undefined ? fallback : parsed;
  } catch {
    return fallback;
  }
}

export function createJournalService(db) {
  return {
    append({
      taskId = null,
      sessionId = null,
      eventType = "note",
      summary = "",
      details = null,
      artifacts = null
    } = {}) {
      const id = `journal-${Date.now()}-${crypto.randomBytes(4).toString("hex")}`;
      db.prepare(`
        INSERT INTO agent_journal (id, task_id, session_id, event_type, summary, details, artifacts, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
      `).run(
        id,
        taskId || null,
        sessionId || null,
        String(eventType || "note"),
        String(summary || ""),
        details === null || details === undefined ? null : JSON.stringify(details),
        artifacts === null || artifacts === undefined ? null : JSON.stringify(artifacts),
        nowIso()
      );
      return id;
    },

    list({ taskId, sessionId, limit = 100 } = {}) {
      const clauses = [];
      const params = [];

      const t = String(taskId || "").trim();
      const s = String(sessionId || "").trim();
      if (t) { clauses.push("task_id = ?"); params.push(t); }
      if (s) { clauses.push("session_id = ?"); params.push(s); }

      const cap = Math.min(Math.max(Number(limit) || 100, 1), 500);
      const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
      const rows = db.prepare(
        `SELECT * FROM agent_journal ${where} ORDER BY created_at DESC LIMIT ?`
      ).all(...params, cap);

      return rows.map((r) => ({
        ...r,
        details: parseMaybeJson(r.details),
        artifacts: parseMaybeJson(r.artifacts)
      }));
    },

    /**
     * 生成"当前 AI 干了什么"的简明摘要，供前端/上下文注入使用
     */
    summarize(limit = 20) {
      const rows = this.list({ limit });
      return rows.map((r) => {
        const detail = r.details && typeof r.details === "object"
          ? Object.entries(r.details)
              .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`)
              .join(" ")
          : "";
        return `[${r.event_type}] ${r.summary}${detail ? " · " + detail : ""}`;
      }).join("\n");
    }
  };
}

// 供 server.mjs 直接导入的模块级函数（绑定到默认实例）
let defaultService = null;
export function bindJournalService(service) {
  defaultService = service;
}

export function appendJournal(entry) {
  if (!defaultService) throw new Error("journal 服务未初始化");
  return defaultService.append(entry);
}

export function listJournal(query) {
  if (!defaultService) throw new Error("journal 服务未初始化");
  return defaultService.list(query);
}

export function summarizeJournal(limit) {
  if (!defaultService) throw new Error("journal 服务未初始化");
  return defaultService.summarize(limit);
}