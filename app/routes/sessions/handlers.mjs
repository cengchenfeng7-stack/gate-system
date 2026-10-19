import { v4 as uuidv4 } from "uuid";
import path from "node:path";
// 会话/群聊路由处理函数 - 从原 server.mjs 完整迁移

import {
  addSessionMember,
  createSession,
  db,
  deleteSession,
  ensureSessionMembers,
  generateGroupNo,
  getActiveSessionMembers,
  getDefaultModel,
  getGroupFilesDir,
  getSession,
  getSessionMemberRecords,
  getWorkspaceRoot,
  initSessionMembers,
  mapGroupFile,
  markFaqHit,
  removeSessionMember,
  searchMessages,
  updateSession,
  updateSessionMember
} from "../../server.mjs";

function safeJson(value, fallback) {
  try { const parsed = JSON.parse(value); return parsed === null || parsed === undefined ? fallback : parsed; } catch { return fallback; }
}
function nowIso() { return new Date().toISOString(); }

export const sessionHandlers = {
  // 会话列表
  async getSessions(req, res) {
    try {
      const sessions = db.prepare("SELECT * FROM sessions ORDER BY updated_at DESC").all();
      res.json({ sessions });
    } catch (error) {
      res.status(500).json({ error: error?.message || "获取会话失败" });
    }
  },

  // 会话详情 + 消息
  async getSession(req, res) {
    try {
      const { sessionId } = req.params;
      const session = getSession(sessionId);
      if (!session) return res.status(404).json({ error: "会话不存在" });
      const messages = db.prepare("SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC").all(sessionId)
        .map((msg) => ({ ...msg, tool_calls: msg.tool_calls ? JSON.parse(msg.tool_calls) : null }));
      res.json({ session, messages });
    } catch (error) {
      res.status(500).json({ error: error?.message || "获取会话失败" });
    }
  },

  // 创建会话
  async createSession(req, res) {
    try {
      const body = req.body || {};
      const model = body.model || getDefaultModel();
      const title = body.title || "新对话";
      const now = nowIso();
      const session = {
        id: uuidv4(),
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
      };
      db.prepare(`
        INSERT INTO sessions (
          id, title, model, sdk_session_id, avatar, category, tags, intro, location, announcement,
          my_nickname, remark, pinned, muted, folded, saved, group_no, member_limit, join_policy,
          created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `).run(
        session.id, session.title, session.model, session.sdk_session_id,
        session.avatar || "👥", session.category || "家庭",
        JSON.stringify(Array.isArray(session.tags) ? session.tags : []),
        session.intro || "", session.location || "", session.announcement || "",
        session.my_nickname || "", session.remark || "", 0, 0, 0, 1,
        session.group_no || generateGroupNo(), Number(session.member_limit) || 20,
        session.join_policy || "manual",
        session.created_at, session.updated_at
      );
      initSessionMembers(session.id, body.memberIds);
      res.json({ session: getSession(session.id) });
    } catch (error) {
      res.status(500).json({ error: error?.message || "创建会话失败" });
    }
  },

  async updateSession(req, res) {
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
      const aliases = { myNickname: "my_nickname", memberLimit: "member_limit", joinPolicy: "join_policy", speechMode: "speech_mode" };
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
      db.prepare(sql).run(...keys.map((k) => updates[k]), nowIso(), session.id);
      res.json({ success: true, session: getSession(session.id) });
    } catch (error) {
      res.status(500).json({ error: error?.message || "更新会话失败" });
    }
  },

  async deleteSession(req, res) {
    try {
      const success = db.prepare("DELETE FROM sessions WHERE id = ?").run(req.params.sessionId).changes > 0;
      if (!success) return res.status(404).json({ error: "会话不存在" });
      db.prepare("DELETE FROM session_agents WHERE session_id = ?").run(req.params.sessionId);
      db.prepare("DELETE FROM group_files WHERE session_id = ?").run(req.params.sessionId);
      res.json({ success: true });
    } catch (error) {
      res.status(500).json({ error: error?.message || "删除会话失败" });
    }
  },

  async markSessionRead(req, res) {
    try {
      const r = db.prepare("UPDATE sessions SET last_read_at = ? WHERE id = ?").run(nowIso(), req.params.sessionId);
      res.json({ success: r.changes > 0 });
    } catch (e) {
      res.status(500).json({ error: e?.message || "标记已读失败" });
    }
  },

  async expandSessionMembers(req, res) {
    try {
      const session = getSession(req.params.sessionId);
      if (!session) return res.status(404).json({ error: "会话不存在" });
      const current = Number(session.member_limit) || 20;
      const target = Math.min(Math.max(Number(req.body?.limit) || 50, current), 500);
      db.prepare("UPDATE sessions SET member_limit = ?, updated_at = ? WHERE id = ?").run(target, nowIso(), session.id);
      res.json({ success: true, memberLimit: target });
    } catch (e) {
      res.status(500).json({ error: e?.message || "扩容失败" });
    }
  },

  async searchMessages(req, res) {
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
  },

  async getSessionMembers(req, res) {
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
  },

  async addSessionMember(req, res) {
    try {
      const agentId = String(req.body?.agentId || "").trim();
      if (!agentId) return res.status(400).json({ error: "请选择 AI 成员" });
      const result = addSessionMember(req.params.sessionId, agentId);
      if (result.error) return res.status(400).json({ error: result.error });
      res.json({ success: true, members: getSessionMemberRecords(req.params.sessionId) });
    } catch (e) {
      res.status(500).json({ error: e?.message || "添加成员失败" });
    }
  },

  async updateSessionMember(req, res) {
    try {
      const result = updateSessionMember(req.params.sessionId, req.params.agentId, req.body || {});
      if (result.error) return res.status(404).json({ error: result.error });
      res.json({ success: true, ...result });
    } catch (e) {
      res.status(500).json({ error: e?.message || "更新成员失败" });
    }
  },

  async removeSessionMember(req, res) {
    try {
      const result = removeSessionMember(req.params.sessionId, req.params.agentId);
      if (!result.success) return res.status(404).json({ error: "该成员不在群内" });
      res.json({ success: true });
    } catch (e) {
      res.status(500).json({ error: e?.message || "移除成员失败" });
    }
  },

  async getGroupFiles(req, res) {
    try {
      const session = getSession(req.params.sessionId);
      if (!session) return res.status(404).json({ error: "会话不存在" });
      const rows = db.prepare("SELECT * FROM group_files WHERE session_id = ? ORDER BY created_at DESC").all(session.id);
      res.json({ success: true, files: rows.map((r) => mapGroupFile(r, session.id)) });
    } catch (e) {
      res.status(500).json({ error: e?.message || "读取群文件失败" });
    }
  },

  async uploadGroupFile(req, res) {
    try {
      const session = getSession(req.params.sessionId);
      if (!session) return res.status(404).json({ error: "会话不存在" });
      const rawName = String(req.body?.name || "file").trim();
      const name = path.basename(rawName).replace(/[<>:"/\\|?*\x00-\x1f]/g, "_").slice(0, 120) || "file";
      let base64 = String(req.body?.dataBase64 || "");
      const comma = base64.indexOf(",");
      if (base64.startsWith("data:") && comma !== -1) base64 = base64.slice(comma + 1);
      if (!base64) return res.status(400).json({ error: "文件内容为空" });
      const buffer = Buffer.from(base64, "base64");
      if (buffer.length === 0) return res.status(400).json({ error: "文件内容为空" });
      if (buffer.length > 10 * 1024 * 1024) return res.status(400).json({ error: "单个群文件最多 10MB" });
      const id = `gf-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const dir = getGroupFilesDir(session.id);
      fs.mkdirSync(dir, { recursive: true });
      const storedName = `${id}-${name}`;
      const abs = path.join(dir, storedName);
      fs.writeFileSync(abs, buffer);
      const storedPath = path.relative(getWorkspaceRoot(), abs).split(path.sep).join("/");
      const createdAt = nowIso();
      db.prepare(`
        INSERT INTO group_files (id, session_id, name, stored_path, size, mime, uploaded_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'human', ?)
      `).run(id, session.id, name, storedPath, buffer.length, String(req.body?.mime || "application/octet-stream"), createdAt);
      const row = db.prepare("SELECT * FROM group_files WHERE id = ?").get(id);
      res.json({ success: true, file: mapGroupFile(row, session.id) });
    } catch (e) {
      res.status(500).json({ error: e?.message || "上传群文件失败" });
    }
  },

  async downloadGroupFile(req, res) {
    try {
      const row = db.prepare("SELECT * FROM group_files WHERE id = ? AND session_id = ?")
        .get(req.params.fileId, req.params.sessionId);
      if (!row) return res.status(404).json({ error: "群文件不存在" });
      const root = getWorkspaceRoot();
      const abs = path.resolve(root, row.stored_path);
      const rel = path.relative(root, abs);
      if (!rel || rel.startsWith("..") || path.isAbsolute(rel) || !fs.existsSync(abs)) {
        return res.status(404).json({ error: "文件已丢失" });
      }
      res.download(abs, row.name);
    } catch (e) {
      res.status(500).json({ error: e?.message || "下载群文件失败" });
    }
  },

  async deleteGroupFile(req, res) {
    try {
      const row = db.prepare("SELECT * FROM group_files WHERE id = ? AND session_id = ?")
        .get(req.params.fileId, req.params.sessionId);
      if (!row) return res.status(404).json({ error: "群文件不存在" });
      try {
        const root = getWorkspaceRoot();
        const abs = path.resolve(root, row.stored_path);
        const rel = path.relative(root, abs);
        if (rel && !rel.startsWith("..") && !path.isAbsolute(rel) && fs.existsSync(abs)) fs.unlinkSync(abs);
      } catch { /* 忽略 */ }
      db.prepare("DELETE FROM group_files WHERE id = ?").run(row.id);
      res.json({ success: true });
    } catch (e) {
      res.status(500).json({ error: e?.message || "删除群文件失败" });
    }
  },

  async getReplySource(req, res) {
    try {
      const msg = db.prepare("SELECT * FROM messages WHERE id = ?").get(req.params.messageId);
      if (!msg) return res.status(404).json({ error: "消息不存在" });
      if (!msg.reply_to_id) return res.json({ success: true, replySource: null });
      const src = db.prepare("SELECT id, role, content, agent_name FROM messages WHERE id = ?").get(msg.reply_to_id);
      res.json({ success: true, replySource: src });
    } catch (e) {
      res.status(500).json({ error: e?.message || "获取引用来源失败" });
    }
  },

  async getAnnouncement(req, res) {
    try {
      const session = getSession(req.params.sessionId);
      if (!session) return res.status(404).json({ error: "会话不存在" });
      res.json({ success: true, announcement: session.announcement || "" });
    } catch (e) {
      res.status(500).json({ error: e?.message || "读取公告失败" });
    }
  },

  // v2.9 消息撤回（软删除）
  async recallMessage(req, res) {
    try {
      const id = String(req.params.messageId || "");
      const msg = db.prepare("SELECT id, session_id FROM messages WHERE id = ?").get(id);
      if (!msg) return res.status(404).json({ error: "消息不存在" });
      const now = nowIso();
      db.prepare("UPDATE messages SET content = '', recalled = 1, recalled_at = ? WHERE id = ?").run(now, id);
      db.prepare("UPDATE sessions SET updated_at = ? WHERE id = ?").run(now, msg.session_id);
      res.json({ success: true, messageId: id, recalled: true });
    } catch (e) {
      res.status(500).json({ error: e?.message || "撤回失败" });
    }
  },

  // v2.9 消息硬删除（仅自己发送的消息）
  async deleteMessage(req, res) {
    try {
      const id = String(req.params.id);
      const msg = db.prepare("SELECT id, session_id, role FROM messages WHERE id = ?").get(id);
      if (!msg) return res.status(404).json({ error: "消息不存在或已撤回" });
      if (msg.role !== "user") return res.status(403).json({ error: "只能删除自己发送的消息" });
      db.prepare("DELETE FROM messages WHERE id = ?").run(id);
      db.prepare("UPDATE messages SET reply_to_id = NULL WHERE reply_to_id = ?").run(id);
      res.json({ success: true, id, sessionId: msg.session_id });
    } catch (e) {
      res.status(500).json({ error: e?.message || "删除失败" });
    }
  }
};

// 从 server.mjs 需要复制的辅助函数（将在 server.mjs 中保留，这里只导入）
// generateGroupNo, ensureSessionMembers, initSessionMembers, getSessionMemberRecords,
// getActiveSessionMembers, addSessionMember, updateSessionMember, removeSessionMember,
// getGroupFilesDir, mapGroupFile, getSession, markFaqHit, getWorkspaceRoot, generateGroupNo,
// ensureSessionMembers, initSessionMembers, getSessionMemberRecords, getActiveSessionMembers,
// addSessionMember, updateSessionMember, removeSessionMember, getGroupFilesDir, mapGroupFile,
// getSession, markFaqHit, getWorkspaceRoot