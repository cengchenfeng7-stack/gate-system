// 笔记服务：CRUD、搜索、导入导出、工具侧 API

import crypto from "crypto";

function nowIso() { return new Date().toISOString(); }
function newId(prefix) { return `${prefix}-${Date.now()}-${crypto.randomBytes(4).toString("hex")}`; }

const VALID_VISIBILITY = ["private", "shared", "agent"];

export function createNotesService(db) {
  const toNote = (row) => ({
    ...row,
    tags: parseTags(row.tags),
    pinned: Boolean(row.pinned)
  });

  const parseTags = (v) => {
    try { const p = JSON.parse(v || "[]"); return Array.isArray(p) ? p : []; } catch { return []; }
  };

  const svc = {
    toNote,

    create(data = {}) {
      const title = String(data.title || "").trim();
      if (!title) throw new Error("标题不能为空");
      const content = String(data.content_md ?? data.content ?? "");
      const id = String(data.id || newId("note"));
      const t = nowIso();

      db.prepare(`
        INSERT INTO notes (id, title, content_md, tags, owner, visibility, task_id, pinned, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `).run(
        id,
        title.slice(0, 200),
        content,
        JSON.stringify(Array.isArray(data.tags) ? data.tags.slice(0, 30).map(String) : []),
        data.owner === "agent" ? "agent" : "human",
        VALID_VISIBILITY.includes(data.visibility) ? data.visibility : "shared",
        data.task_id || null,
        data.pinned ? 1 : 0,
        data.created_at || t,
        t
      );

      return toNote(db.prepare("SELECT * FROM notes WHERE id = ?").get(id));
    },

    get(id) {
      const row = db.prepare("SELECT * FROM notes WHERE id = ?").get(String(id || ""));
      return row ? toNote(row) : null;
    },

    search({ q, owner, visibility, limit = 200 } = {}) {
      const clauses = [];
      const params = [];

      const keyword = String(q || "").trim();
      if (keyword) {
        clauses.push("(title LIKE ? OR content_md LIKE ?)");
        params.push(`%${keyword}%`, `%${keyword}%`);
      }
      if (owner) { clauses.push("owner = ?"); params.push(String(owner)); }
      if (visibility) { clauses.push("visibility = ?"); params.push(String(visibility)); }

      const cap = Math.min(Math.max(Number(limit) || 200, 1), 500);
      const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
      const rows = db.prepare(
        `SELECT * FROM notes ${where} ORDER BY pinned DESC, updated_at DESC LIMIT ?`
      ).all(...params, cap);
      return rows.map(toNote);
    },

    update(id, data = {}) {
      const existing = this.get(id);
      if (!existing) return null;

      const title = data.title !== undefined
        ? String(data.title || "").trim()
        : existing.title;
      if (!title) throw new Error("标题不能为空");

      const content = data.content_md !== undefined
        ? String(data.content_md || "")
        : (data.content !== undefined ? String(data.content || "") : existing.content_md);

      const tags = Array.isArray(data.tags)
        ? JSON.stringify(data.tags.slice(0, 30).map(String))
        : JSON.stringify(existing.tags);

      const visibility = VALID_VISIBILITY.includes(data.visibility)
        ? data.visibility
        : existing.visibility;

      const owner = data.owner === "agent" ? "agent" : existing.owner;
      const pinned = data.pinned !== undefined ? (data.pinned ? 1 : 0) : (existing.pinned ? 1 : 0);
      const taskId = data.task_id !== undefined ? (data.task_id || null) : existing.task_id;

      db.prepare(`
        UPDATE notes SET title = ?, content_md = ?, tags = ?, owner = ?, visibility = ?,
                         task_id = ?, pinned = ?, updated_at = ?
        WHERE id = ?
      `).run(title.slice(0, 200), content, tags, owner, visibility, taskId, pinned, nowIso(), existing.id);

      return this.get(existing.id);
    },

    remove(id) {
      return db.prepare("DELETE FROM notes WHERE id = ?").run(String(id || "")).changes > 0;
    },

    exportAll() {
      const rows = db.prepare("SELECT * FROM notes ORDER BY updated_at DESC").all().map(toNote);
      return { version: 1, exportedAt: nowIso(), notes: rows };
    },

    importMany(items = []) {
      let added = 0, updated = 0, skipped = 0;

      const run = db.transaction((list) => {
        for (const item of list.slice(0, 500)) {
          const title = String(item?.title || "").trim();
          const content = String(item?.content_md ?? item?.content ?? "").trim();
          if (!title || !content) { skipped++; continue; }

          const existing = item.id
            ? db.prepare("SELECT id FROM notes WHERE id = ?").get(String(item.id))
            : null;

          if (existing) {
            db.prepare(`
              UPDATE notes SET title = ?, content_md = ?, tags = ?, owner = ?, visibility = ?,
                               task_id = ?, pinned = ?, updated_at = ?
              WHERE id = ?
            `).run(
              title.slice(0, 200), content,
              JSON.stringify(Array.isArray(item.tags) ? item.tags.slice(0, 30).map(String) : []),
              item.owner === "agent" ? "agent" : "human",
              VALID_VISIBILITY.includes(item.visibility) ? item.visibility : "shared",
              item.task_id || null,
              item.pinned ? 1 : 0, nowIso(), existing.id
            );
            updated++;
          } else {
            const id = item.id ? String(item.id) : newId("note");
            db.prepare(`
              INSERT INTO notes (id, title, content_md, tags, owner, visibility, task_id, pinned, created_at, updated_at)
              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            `).run(
              id, title.slice(0, 200), content,
              JSON.stringify(Array.isArray(item.tags) ? item.tags.slice(0, 30).map(String) : []),
              item.owner === "agent" ? "agent" : "human",
              VALID_VISIBILITY.includes(item.visibility) ? item.visibility : "shared",
              item.task_id || null, item.pinned ? 1 : 0,
              item.created_at || nowIso(), nowIso()
            );
            added++;
          }
        }
      });

      run(items);
      return { added, updated, skipped };
    }
  };

  // 模块级便捷导出（server.mjs 直接引用）
  svc.toNote = toNote;
  return svc;
}

// 供 server.mjs 直接导入的模块级函数（绑定到默认实例）
let defaultService = null;
export function bindNotesService(service) {
  defaultService = service;
}

export function createNoteRecord(data) {
  if (!defaultService) throw new Error("notes 服务未初始化");
  return defaultService.create(data);
}

export function getNoteRecord(id) {
  if (!defaultService) throw new Error("notes 服务未初始化");
  return defaultService.get(id);
}

export function searchNoteRecords(query) {
  if (!defaultService) throw new Error("notes 服务未初始化");
  return defaultService.search(query);
}

export function toNote(row) {
  if (!defaultService) throw new Error("notes 服务未初始化");
  return defaultService.toNote(row);
}

/**
 * 给模型侧工具调用用的笔记 API
 */
export function notesToolApi(service) {
  return {
    async run(args = {}) {
      const action = String(args.action || "list").trim();

      switch (action) {
        case "create":
          return { note: service.create(args) };

        case "get":
          return { note: service.get(args.id) };

        case "list":
          return { notes: service.search(args) };

        case "update":
          return { note: service.update(args.id, args) };

        case "delete":
          return { deleted: service.remove(args.id) };

        case "search":
          return { notes: service.search({ q: args.q, limit: args.limit }) };

        default:
          throw new Error(`未知笔记操作: ${action}`);
      }
    }
  };
}