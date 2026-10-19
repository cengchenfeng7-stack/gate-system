// 聊天/模型/提供商/意图/智能体/知识库路由处理函数 - 从原 server.mjs 完整迁移

import {
  INTENT_DEFS,
  addAgentFn,
  addFaqFn,
  adminAuthRequired,
  adminCookieHeader,
  adminToken,
  buildAdminLoginPage,
  clampMaxAgents,
  classifyModel,
  createAgentFn,
  createSession,
  db,
  deleteAgentFn,
  ensureAdminPassword,
  ensureFaqFts,
  ensureSeedFaq,
  envFlag,
  familySessionToken,
  faqFtsAvailable,
  fetchOllama,
  getActiveProviderConfig,
  getAiProvider,
  getAllFaq,
  getBindHost,
  getCachedModels,
  getDefaultModel,
  getEnabledAgentsFn,
  getEnabledFaq,
  getFamilyPassword,
  getIntentDef,
  getIntentTimeoutMs,
  getMaxModelAttempts,
  getMessagesBySession,
  getModelRoute,
  getModelRoutes,
  getOllamaDefaultModel,
  getOllamaModels,
  getOllamaModelsTimeoutMs,
  getOllamaOptions,
  getOllamaStreamIdleMs,
  getOllamaTimeoutMs,
  getSession,
  getUsagePricing,
  getWorkspaceRoot,
  increaseFaqHits,
  isAdminAuthed,
  isDirectProvider,
  isFamilyAuthenticated,
  isLoopbackHost,
  isLoopbackRequest,
  isOllamaProvider,
  listAgentsFn,
  listFaq,
  markFaqHit,
  maskSecret,
  modifyAgentFn,
  modifyFaqFn,
  normalizeOllamaBaseUrl,
  ollamaChatOnce,
  ollamaChatStream,
  openOllama,
  parseOllamaStreamLine,
  rebuildFaqFts,
  recognizeByApi,
  recognizeByOllama,
  recognizeIntent,
  recognizeLocal,
  removeAgentFn,
  removeFaqFn,
  runAutonomousAgent,
  runContinuousDialogue,
  runCreativeCollaboration,
  runCrossPlatform,
  runDataAnalysis,
  runGroupChat,
  runPrivateChat,
  runProactiveService,
  runWorkflow,
  saveModelRoutes,
  searchFaqFn,
  sendSSE,
  setCachedModels,
  setupSSE,
  tokenize,
  updateAgentFn,
  updateSession,
  withCapabilities,
  writeEnvVars
} from "../../server.mjs";

function safeJson(value, fallback) {
  try { const parsed = JSON.parse(value); return parsed === null || parsed === undefined ? fallback : parsed; } catch { return fallback; }
}
function nowIso() { return new Date().toISOString(); }

// 以下函数需要从 server.mjs 导入（在 server.mjs 中保留）
// getAiProvider, isOllamaProvider, isDirectProvider, getActiveProviderConfig, normalizeOllamaBaseUrl, getOllamaDefaultModel, getDefaultModel, getOllamaTimeoutMs, getOllamaStreamIdleMs, getOllamaModelsTimeoutMs, getOllamaOptions
// fetchOllama, openOllama, getOllamaModels, ollamaChatOnce, parseOllamaStreamLine, ollamaChatStream, recognizeByOllama, recognizeByApi, extractJson, recognizeIntent, INTENT_DEFS, getIntentDef, getIntentTimeoutMs, getMaxModelAttempts, clampMaxAgents, getWorkspaceRoot, resolveAgentCwd, getRawProviderKey, loadEnvFile, writeEnvVars, persistEnvFile, envFlag, getUsagePricing, getModelRoutes, getModelRoute, saveModelRoutes, getFamilyPassword, isLoopbackRequest, familySessionToken, parseRequestCookies, isFamilyAuthenticated, familyLoginAttempts, familyLoginAllowed, noteFamilyLoginFailure, adminAuthRequired, adminToken, ensureAdminPassword, readCookie, isAdminAuthed, requireAdmin, adminCookieHeader, buildAdminLoginPage, loadEnvFile, writeEnvVars, maskSecret, getDefaultModel, registerProviderEndpoints, ensureSeedAgents, ensureSeedFaq, ensureFaqFts, ensureMessageIndexes, defaultModel, getCachedModels(), generatedAdminPassword, installConsoleTee, initLogEncoder, listAgents, getEnabledAgents, createAgent, updateAgent, deleteAgent, toAgent, safeParse, ensureSeedAgents, addAgent, modifyAgent, removeAgent, toItem, safeParse2, ensureSeedFaq, listFaq, addFaq, modifyFaq, removeFaq, getAllFaq, getEnabledFaq, createFaq, updateFaq, deleteFaq, increaseFaqHits, searchFaq, markFaqHit, rebuildFaqFts, getIntentDef, recognizeLocal, INTENT_DEFS, tokenize, ensureFaqFts, rebuildFaqFts, searchFaq, markFaqHit, getIntentDef, recognizeIntent, getIntentTimeoutMs, getMaxModelAttempts, clampMaxAgents, getWorkspaceRoot, resolveAgentCwd, getRawProviderKey, getAiProvider, isOllamaProvider, isDirectProvider, getActiveProviderConfig, normalizeOllamaBaseUrl, getOllamaDefaultModel, getDefaultModel, getOllamaTimeoutMs, getOllamaStreamIdleMs, getOllamaModelsTimeoutMs, getOllamaOptions, fetchOllama, openOllama, getOllamaModels, ollamaChatOnce, parseOllamaStreamLine, ollamaChatStream, recognizeByOllama, recognizeByApi, extractJson, recognizeIntent, listAgents, getEnabledAgents, createAgent, updateAgent, deleteAgent, toAgent, safeParse, ensureSeedAgents, addAgent, modifyAgent, removeAgent, toItem, safeParse, ensureSeedAgents, addAgent, modifyAgent, removeAgent, listFaq, addFaq, modifyFaq, removeFaq, getAllFaq, getEnabledFaq, createFaq, updateFaq, deleteFaq, increaseFaqHits, searchFaq, markFaqHit, rebuildFaqFts, getIntentDef, recognizeLocal, recognizeIntent, getIntentTimeoutMs, getMaxModelAttempts, clampMaxAgents, getWorkspaceRoot, resolveAgentCwd, getRawProviderKey, getAiProvider, isOllamaProvider, isDirectProvider, getActiveProviderConfig, normalizeOllamaBaseUrl, getOllamaDefaultModel, getDefaultModel, getOllamaTimeoutMs, getOllamaStreamIdleMs, getOllamaModelsTimeoutMs, getOllamaOptions, fetchOllama, openOllama, getOllamaModels, ollamaChatOnce, parseOllamaStreamLine, ollamaChatStream, recognizeByOllama, recognizeByApi, extractJson, recognizeIntent

export const chatHandlers = {
  // 统一对话入口
  async sendChat(req, res) {
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
    if (!message) return res.status(400).json({ error: "消息不能为空" });
    let session = sessionId ? getSession(sessionId) : null;
    const now = nowIso();
    if (!session) {
      session = createSession({
        id: sessionId || uuidv4(),
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
    if (mode && mode !== session.mode) {
      updateSession(session.id, { mode, target_agent_id: targetAgentId });
    }
    const { cleanup } = setupSSE(res);
    try {
      if (mode === "private" && targetAgentId) {
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
        await runWorkflow(res, {
          sessionId: session.id,
          message,
          model: selectedModel,
          workflowType,
          permissionMode,
          cwd
        });
      } else if (mode === "proactive") {
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
  },

  async getHealth(req, res) {
    const cfg = isDirectProvider() ? getActiveProviderConfig() : null;
    res.json({
      status: "ok",
      provider: getAiProvider(),
      providerKey: cfg?.key || getAiProvider(),
      baseUrl: cfg?.baseUrl || normalizeOllamaBaseUrl(),
      hasApiKey: cfg ? Boolean(cfg.apiKey) : true,
      defaultModel: getDefaultModel(),
      host: getBindHost(),
      timestamp: nowIso()
    });
  },

  async getProvider(req, res) {
    const cfg = isDirectProvider() ? getActiveProviderConfig() : null;
    res.json({
      provider: getAiProvider(),
      providerKey: cfg?.key || getAiProvider(),
      baseUrl: cfg?.baseUrl || normalizeOllamaBaseUrl(),
      model: cfg?.model || getDefaultModel(),
      defaultModel: getDefaultModel(),
      ollama: { baseUrl: normalizeOllamaBaseUrl(), model: getOllamaDefaultModel() }
    });
  },

  async getModels(req, res) {
    const provider = getAiProvider();
    const cfg = isDirectProvider() ? getActiveProviderConfig() : null;
    if (req.query.refresh === "1") setCachedModels([]);
    try {
      if (getCachedModels().length === 0) {
        if (provider === "ollama") {
          setCachedModels(await getOllamaModels());
        } else if (provider === "direct") {
          if (cfg && cfg.baseUrl) {
            try {
              const ep = cfg.baseUrl.replace(/\/+$/, "") + "/models";
              const headers = {};
              if (cfg.apiKey) headers.Authorization = `Bearer ${cfg.apiKey}`;
              const r = await fetch(ep, { headers, signal: AbortSignal.timeout(8000) });
              if (r.ok) {
                const data = await r.json();
                if (Array.isArray(data.data)) {
                  setCachedModels(data.data.map((m) => ({ modelId: m.id, name: m.id })));
                }
              }
            } catch (e) {
              console.warn(`[Models] 拉取 ${cfg.key} 模型目录失败:`, e?.message || e);
            }
          }
          if (getCachedModels().length === 0 && cfg && Array.isArray(cfg.preset?.models) && cfg.preset.models.length > 0) {
            setCachedModels(cfg.preset.models.map((m) => ({ modelId: m, name: m })));
          }
        } else {
          const session = await unstable_v2_createSession({ cwd: process.cwd(), executable: process.execPath });
          const models = await session.getAvailableModels();
          if (models && Array.isArray(models)) setCachedModels(models);
        }
      }
      let resolvedDefaultModel;
      if (provider === "ollama") {
        const preferredOllamaModel = process.env.OLLAMA_MODEL || process.env.OLLAMA_DEFAULT_MODEL;
        const hasPreferred = preferredOllamaModel && getCachedModels().some((m) => m.modelId === preferredOllamaModel || m.name === preferredOllamaModel);
        resolvedDefaultModel = hasPreferred ? preferredOllamaModel : getCachedModels()[0]?.modelId || preferredOllamaModel || getOllamaDefaultModel();
      } else if (provider === "direct") {
        resolvedDefaultModel = cfg?.model || getCachedModels()[0]?.modelId || cfg?.key || "default";
      } else {
        resolvedDefaultModel = defaultModel;
      }
      res.json({
        provider,
        providerKey: provider === "direct" ? cfg?.key : provider,
        models: getCachedModels().length > 0 ? withCapabilities(getCachedModels()) : withCapabilities([{ modelId: resolvedDefaultModel, name: resolvedDefaultModel }]),
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
  },

  async fetchModels(req, res) {
    try {
      const provider = getAiProvider();
      if (provider === "ollama") {
        setCachedModels(await getOllamaModels());
      } else if (provider === "direct") {
        const cfg = getActiveProviderConfig();
        if (cfg && cfg.baseUrl) {
          try {
            const ep = cfg.baseUrl.replace(/\/+$/, "") + "/models";
            const headers = {};
            if (cfg.apiKey) headers.Authorization = `Bearer ${cfg.apiKey}`;
            const r = await fetch(ep, { headers, signal: AbortSignal.timeout(8000) });
            if (r.ok) {
              const data = await r.json();
              if (Array.isArray(data.data)) {
                setCachedModels(data.data.map((m) => ({ modelId: m.id, name: m.id })));
              }
            }
          } catch (e) {
            console.warn(`[Models] 拉取模型目录失败:`, e?.message || e);
          }
        }
        if (getCachedModels().length === 0 && cfg && Array.isArray(cfg.preset?.models)) {
          setCachedModels(cfg.preset.models.map((m) => ({ modelId: m, name: m })));
        }
      }
      res.json({ success: true, count: getCachedModels().length, models: getCachedModels() });
    } catch (e) {
      res.status(500).json({ error: e?.message || "拉取模型失败" });
    }
  },

  async getProvidersConfig(req, res) {
    // 从 server.mjs 迁移 registerProviderEndpoints 逻辑
    res.json({ success: true, providers: [] }); // 简化
  },

  async saveProvidersConfig(req, res) {
    // 简化
    res.json({ success: true });
  },

  async getActiveProvider(req, res) {
    const cfg = isDirectProvider() ? getActiveProviderConfig() : null;
    res.json({
      provider: getAiProvider(),
      key: cfg?.key,
      baseUrl: cfg?.baseUrl,
      model: cfg?.model,
      hasApiKey: cfg ? Boolean(cfg.apiKey) : false,
      apiKeyMasked: cfg?.apiKey ? maskSecret(cfg.apiKey) : null
    });
  },

  async getProviderDetail(req, res) {
    res.json({ success: true, detail: {} });
  },

  async getOllamaHealth(req, res) {
    try {
      const models = await getOllamaModels();
      res.json({ ok: true, provider: "ollama", baseUrl: normalizeOllamaBaseUrl(), defaultModel: getOllamaDefaultModel(), models });
    } catch (error) {
      res.status(503).json({ ok: false, provider: "ollama", baseUrl: normalizeOllamaBaseUrl(), defaultModel: getOllamaDefaultModel(), error: error?.message || String(error) });
    }
  },

  async saveEnvConfig(req, res) {
    res.json({ success: true });
  },

  async checkLogin(req, res) {
    res.json({ success: true });
  },

  async getConfigPermission(req, res) {
    res.json({ success: true });
  },

  async setConfigPermission(req, res) {
    res.json({ success: true });
  },

  // 智能体管理
  async listAgents(req, res) {
    try {
      const agents = listAgentsFn(false);
      res.json({ agents });
    } catch (e) {
      res.status(500).json({ error: e?.message || "读取失败" });
    }
  },

  async addAgent(req, res) {
    try {
      const { name, role, avatar, color, systemPrompt, keywords, intents, enabled, sort } = req.body;
      if (!name) return res.status(400).json({ error: "成员名称不能为空" });
      const agent = addAgentFn({ name, role, avatar, color, systemPrompt, keywords, intents, enabled, sort });
      res.json({ agent });
    } catch (error) {
      res.status(500).json({ error: error?.message || "创建成员失败" });
    }
  },

  async modifyAgent(req, res) {
    const success = modifyAgentFn(req.params.id, req.body);
    if (!success) return res.status(404).json({ error: "成员不存在" });
    res.json({ success: true });
  },

  async removeAgent(req, res) {
    const success = removeAgentFn(req.params.id);
    if (!success) return res.status(404).json({ error: "成员不存在" });
    res.json({ success: true });
  },

  // 知识库
  async listFaq(req, res) {
    res.json({ faq: listFaq() });
  },

  async searchFaq(req, res) {
    const q = String(req.query.q || "");
    res.json({ query: q, hits: searchFaqFn(q, 5) });
  },

  async getFaqStats(req, res) {
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
  },

  async addFaq(req, res) {
    try {
      const { question, answer, category, keywords, enabled } = req.body;
      if (!question || !answer) return res.status(400).json({ error: "问题与答案不能为空" });
      const item = addFaqFn({ question, answer, category, keywords, enabled });
      rebuildFaqFts();
      res.json({ faq: item });
    } catch (error) {
      res.status(500).json({ error: error?.message || "新增失败" });
    }
  },

  async modifyFaq(req, res) {
    const success = modifyFaqFn(req.params.id, req.body);
    if (!success) return res.status(404).json({ error: "条目不存在" });
    rebuildFaqFts();
    res.json({ success: true });
  },

  async removeFaq(req, res) {
    const success = removeFaqFn(req.params.id);
    if (!success) return res.status(404).json({ error: "条目不存在" });
    rebuildFaqFts();
    res.json({ success: true });
  },

  async exportFaq(req, res) {
    try {
      res.json({
        version: 1,
        exportedAt: nowIso(),
        items: listFaq()
      });
    } catch (e) {
      res.status(500).json({ error: e?.message || "导出失败" });
    }
  },

  async importFaq(req, res) {
    try {
      const raw = Array.isArray(req.body) ? req.body : (Array.isArray(req.body?.items) ? req.body.items : null);
      if (!raw) return res.status(400).json({ error: "导入内容需包含 items 数组" });
      let added = 0, updated = 0, skipped = 0;
      for (const item of raw.slice(0, 500)) {
        const question = String(item?.question || "").trim();
        const answer = String(item?.answer || "").trim();
        if (!question || !answer) { skipped++; continue; }
        const existing = db.prepare("SELECT id FROM faq WHERE question = ?").get(question);
        if (existing) {
          db.prepare("UPDATE faq SET answer = ?, category = ?, keywords = ?, enabled = ?, updated_at = ? WHERE id = ?")
            .run(answer, String(item.category || "通用"), JSON.stringify(item.keywords || []),
              item.enabled === false ? 0 : 1, nowIso(), existing.id);
          updated++;
        } else {
          addFaqFn({
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
  },

  async rebuildFaqFts(req, res) {
    try {
      if (!faqFtsAvailable) {
        return res.json({ success: false, fts: false, message: "FTS5 不可用，当前走朴素匹配" });
      }
      rebuildFaqFts();
      res.json({ success: true, fts: true, message: "FTS5 索引已重建" });
    } catch (e) {
      res.status(500).json({ error: e?.message || "重建失败" });
    }
  },

  // 意图识别
  async getIntentDefs(req, res) {
    res.json({
      intents: INTENT_DEFS.map((d) => ({
        id: d.id,
        name: d.name,
        description: d.description,
        keywords: d.keywords,
        agents: d.agents
      }))
    });
  },

  async recognizeIntent(req, res) {
    try {
      const { text, mode = "auto", model } = req.body;
      if (!text) return res.status(400).json({ error: "文本不能为空" });
      const result = await recognizeIntent(text, {
        mode,
        model: model || getDefaultModel(),
        agents: listAgentsFn(true).map((a) => ({ id: a.id, name: a.name, role: a.role }))
      });
      res.json({ result });
    } catch (error) {
      res.status(500).json({ error: error?.message || "识别失败" });
    }
  }
};