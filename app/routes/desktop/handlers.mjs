import {
  db,
  isLoopbackRequest,
  mapNotification,
  nowIso
} from "../../server.mjs";
// 桌面壳专用接口处理函数

export const desktopHandlers = {
  async getDesktopNotifications(req, res) {
    if (!isLoopbackRequest(req)) return res.status(403).json({ error: "仅允许本机访问" });
    try {
      const after = String(req.query.after || "1970-01-01T00:00:00.000Z");
      const rows = db.prepare("SELECT * FROM notifications WHERE created_at > ? ORDER BY created_at ASC LIMIT 20").all(after);
      const unread = db.prepare("SELECT COUNT(*) AS c FROM notifications WHERE read = 0").get().c;
      res.json({ success: true, notifications: rows.map(mapNotification), unreadCount: unread });
    } catch (e) {
      res.status(500).json({ error: e?.message || "读取桌面通知失败" });
    }
  },

  async postDesktopSelection(req, res) {
    if (!isLoopbackRequest(req)) return res.status(403).json({ error: "仅允许本机访问" });
    try {
      const text = String(req.body?.text || "").trim();
      if (!text) return res.status(400).json({ error: "选中内容为空" });
      if (text.length > 12000) return res.status(400).json({ error: "选中内容过长（最多 12000 字符）" });
      pendingDesktopSelection = {
        id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
        text,
        createdAt: nowIso()
      };
      res.json({ success: true, id: pendingDesktopSelection.id });
    } catch (e) {
      res.status(500).json({ error: e?.message || "保存划词内容失败" });
    }
  },

  async getDesktopSelection(req, res) {
    if (!isLoopbackRequest(req)) return res.status(403).json({ error: "仅允许本机访问" });
    const after = String(req.query.after || "");
    const item = pendingDesktopSelection && pendingDesktopSelection.id !== after ? pendingDesktopSelection : null;
    res.json({ success: true, selection: item });
  },

  async ackDesktopSelection(req, res) {
    if (!isLoopbackRequest(req)) return res.status(403).json({ error: "仅允许本机访问" });
    const id = String(req.body?.id || "");
    if (pendingDesktopSelection && pendingDesktopSelection.id === id) pendingDesktopSelection = null;
    res.json({ success: true });
  }
};

// 从 server.mjs 导入: isLoopbackRequest, db, mapNotification, pendingDesktopSelection, nowIso