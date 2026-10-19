// 聊天/模型路由

import { asyncHandler } from "../middleware/error.mjs";
import { validateBody, validateParams, validateQuery } from "../middleware/validation.mjs";
import { requireFamily, requireAdmin } from "../middleware/auth.mjs";

export function registerChatRoutes(app, handlers) {
  const {
    sendChat,
    getHealth,
    getProvider,
    getModels,
    fetchModels,
    getProvidersConfig,
    saveProvidersConfig,
    getActiveProvider,
    getProviderDetail,
    getOllamaHealth,
    saveEnvConfig,
    getConfigPermission,
    setConfigPermission,
    checkLogin,
    getIntentDefs,
    recognizeIntent,
    listAgents,
    addAgent,
    modifyAgent,
    removeAgent,
    listFaq,
    searchFaq,
    getFaqStats,
    addFaq,
    modifyFaq,
    removeFaq,
    exportFaq,
    importFaq,
    rebuildFaqFts
  } = handlers;

  // 健康检查
  app.get("/api/health", asyncHandler(getHealth));
  
  // 当前提供商
  app.get("/api/provider", asyncHandler(getProvider));
  
  // 模型列表
  app.get("/api/models", asyncHandler(getModels));
  app.get("/api/models/fetch", asyncHandler(fetchModels));
  
  // 提供商配置
  app.get("/api/providers/config", asyncHandler(getProvidersConfig));
  app.post("/api/providers/config", requireAdmin, validateBody(["providerKey", "?apiKey", "?baseUrl", "?model"]), asyncHandler(saveProvidersConfig));
  app.get("/api/providers/active", asyncHandler(getActiveProvider));
  app.get("/api/providers/detail", validateQuery(["key"]), asyncHandler(getProviderDetail));
  
  // Ollama
  app.get("/api/ollama/health", asyncHandler(getOllamaHealth));
  
  // 统一对话入口
  app.post("/api/chat", asyncHandler(sendChat));
  
  // 意图识别
  app.get("/api/intent/defs", asyncHandler(getIntentDefs));
  app.post("/api/intent/recognize", asyncHandler(recognizeIntent));
  
  // 智能体管理
  app.get("/api/agents", asyncHandler(listAgents));
  app.post("/api/agents", requireAdmin, asyncHandler(addAgent));
  app.patch("/api/agents/:id", requireAdmin, asyncHandler(modifyAgent));
  app.delete("/api/agents/:id", requireAdmin, asyncHandler(removeAgent));
  
  // 知识库
  app.get("/api/faq", asyncHandler(listFaq));
  app.get("/api/faq/search", asyncHandler(searchFaq));
  app.get("/api/faq/stats", asyncHandler(getFaqStats));
  app.post("/api/faq", asyncHandler(addFaq));
  app.patch("/api/faq/:id", asyncHandler(modifyFaq));
  app.delete("/api/faq/:id", asyncHandler(removeFaq));
  app.get("/api/faq/export", asyncHandler(exportFaq));
  app.post("/api/faq/import", asyncHandler(importFaq));
  app.post("/api/faq/rebuild-fts", asyncHandler(rebuildFaqFts));
  
  // 环境配置
  app.post("/api/save-env-config", requireAdmin, asyncHandler(saveEnvConfig));
  app.get("/api/check-login", asyncHandler(checkLogin));
  
  // 权限档位
  app.get("/api/config-permission", asyncHandler(getConfigPermission));
  app.post("/api/config-permission", requireAdmin, asyncHandler(setConfigPermission));
}