import {
  adminAuthRequired,
  adminCookieHeader,
  adminToken,
  buildAdminLoginPage,
  countIntentsBySource,
  countIntentsByType,
  db,
  deleteSession,
  envFlag,
  familySessionToken,
  getAdminSessions,
  getAdminStats,
  getAiProvider,
  getBindHost,
  getDefaultModel,
  getFamilyPassword,
  getIntentLogs,
  getModelRoute,
  getModelRoutes,
  getOllamaDefaultModel,
  getOllamaModels,
  getUsagePricing,
  isAdminAuthed,
  isFamilyAuthenticated,
  isLoopbackHost,
  isLoopbackRequest,
  isOllamaProvider,
  mapNotification,
  maskSecret,
  normalizeOllamaBaseUrl,
  nowIso,
  saveModelRoutes,
  searchMessages,
  writeEnvVars
} from "../../server.mjs";
// 管理后台/桌面壳路由处理函数

export const adminHandlers = {
  async getAdminAuth(req, res) {
    res.json({ required: adminAuthRequired(), authed: isAdminAuthed(req), host: getBindHost() });
  },

  async adminLogin(req, res) {
    if (!adminAuthRequired()) {
      return res.json({ success: true, required: false, message: "当前未开启管理后台鉴权" });
    }
    const password = String((req.body || {}).password || "");
    if (!password || password !== String(process.env.ADMIN_PASSWORD || "")) {
      return res.status(401).json({ error: "管理密码错误，请重试" });
    }
    res.setHeader("Set-Cookie", adminCookieHeader());
    res.json({ success: true, required: true });
  },

  async adminLogout(req, res) {
    res.setHeader("Set-Cookie", "admin_token=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0");
    res.json({ success: true });
  },

  async getAdminOverview(req, res) {
    try {
      const stats = getAdminStats();
      const bySource = countIntentsBySource();
      const byType = countIntentsByType();
      const sourceMap = {};
      bySource.forEach((s) => { sourceMap[s.source] = s.count; });
      res.json({
        stats,
        intentSource: sourceMap,
        intentTypes: byType,
        recentSessions: getAdminSessions().slice(0, 8)
      });
    } catch (error) {
      res.status(500).json({ error: error?.message || "获取统计失败" });
    }
  },

  async getAdminSessions(req, res) {
    res.json({ sessions: getAdminSessions() });
  },

  async deleteAdminSession(req, res) {
    const success = deleteSession(req.params.id);
    res.json({ success });
  },

  async getAdminMessages(req, res) {
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
  },

  async getAdminIntents(req, res) {
    const limit = req.query.limit ? Number(req.query.limit) : 100;
    res.json({ logs: getIntentLogs(limit) });
  },

  async exportAdminChat(req, res) {
    const sessionId = req.query.sessionId ? String(req.query.sessionId) : void 0;
    const messages = searchMessages({ sessionId, limit: 1e3 });
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.setHeader("Content-Disposition", `attachment; filename="chat-export-${Date.now()}.json"`);
    res.end(JSON.stringify({ exportedAt: nowIso(), messages }, null, 2));
  },

  async checkLogin(req, res) {
    const response = { isLoggedIn: false, envConfigured: false, cliConfigured: false, envVars: {}, provider: getAiProvider(), defaultModel: getDefaultModel() };
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
    const apiKey = process.env.CODEBUDDY_API_KEY;
    const authToken = process.env.CODEBUDDY_AUTH_TOKEN;
    if (apiKey || authToken) {
      response.envConfigured = true;
      if (apiKey) response.envVars.apiKey = maskSecret(apiKey);
      if (authToken) response.envVars.authToken = maskSecret(authToken);
    }
    try {
      let needsLogin = false;
      const result = await unstable_v2_authenticate({
        environment: "external",
        executable: process.execPath,
        onAuthUrl: async () => { needsLogin = true; }
      });
      if (!needsLogin && result?.userinfo) {
        response.isLoggedIn = true;
        response.cliConfigured = true;
        response.method = response.envConfigured ? "env" : "cli";
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
  },

  async saveEnvConfig(req, res) {
    res.json({ success: true });
  },

  async getConfigPermission(req, res) {
    res.json({ success: true });
  },

  async setConfigPermission(req, res) {
    res.json({ success: true });
  }
};

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

// 从 server.mjs 导入的函数/变量
// adminAuthRequired, isAdminAuthed, getBindHost, adminCookieHeader, adminToken, maskSecret, getDefaultModel, isOllamaProvider, normalizeOllamaBaseUrl, getOllamaDefaultModel, getOllamaModels, unstable_v2_createSession, unstable_v2_authenticate, maskSecret, getAiProvider, getAdminStats, countIntentsBySource, countIntentsByType, getAdminSessions, deleteSession, searchMessages, getIntentLogs, buildAdminLoginPage, loadEnvFile, writeEnvVars, envFlag, getUsagePricing, getModelRoutes, getModelRoute, saveModelRoutes, getFamilyPassword, isLoopbackRequest, familySessionToken, parseRequestCookies, isFamilyAuthenticated, isLoopbackRequest, adminAuthRequired, adminToken, requireAdmin, getBindHost, isLoopbackHost, getAdminStats, countIntentsBySource, countIntentsByType, getAdminSessions, searchMessages, getIntentLogs, maskSecret, getDefaultModel, getAiProvider, isOllamaProvider, normalizeOllamaBaseUrl, getOllamaDefaultModel, getOllamaModels, maskSecret, unstable_v2_createSession, unstable_v2_authenticate, getDefaultModel, getBindHost, adminAuthRequired, isAdminAuthed, adminCookieHeader, adminToken, maskSecret, nowIso, isLoopbackRequest, getBindHost, isLoopbackHost, adminAuthRequired, isAdminAuthed, adminCookieHeader, adminToken, maskSecret, getDefaultModel, getAiProvider, isOllamaProvider, normalizeOllamaBaseUrl, getOllamaDefaultModel, getOllamaModels, unstable_v2_createSession, unstable_v2_authenticate, getDefaultModel, maskSecret, getBindHost, isLoopbackHost, adminAuthRequired, isAdminAuthed, adminCookieHeader, adminToken, getDefaultModel, getAiProvider, isOllamaProvider, getOllamaModels, maskSecret, getDefaultModel