// 工具/设置/开放接口路由

import { asyncHandler } from "../middleware/error.mjs";
import { validateBody, validateParams, validateQuery } from "../middleware/validation.mjs";
import { requireFamily, requireAdmin, requireOpenApiKey } from "../middleware/auth.mjs";

export function registerToolsRoutes(app, handlers) {
  const {
    // 工具页
    translateText,
    textAction,
    takeScreenshot,
    getRecentScreenshots,
    getWorkspaceFiles,
    readWorkspaceFile,
    // 编码搭档
    getCodingAgents,
    runCoding,
    getCodingEvents,
    stopCoding,
    getCodingRuns,
    // 笔记/工作日志
    getNotes,
    exportNotes,
    importNotes,
    getNote,
    createNote,
    updateNote,
    deleteNote,
    getJournal,
    writeJournal,
    serveScreenshot
  } = handlers;

  // 翻译
  app.post("/api/tools/translate", requireFamily, validateBody(["text", "?targetLang"]), asyncHandler(translateText));
  
  // 划词操作
  app.post("/api/tools/text-action", requireFamily, validateBody(["text", "action"]), asyncHandler(textAction));
  
  // 截图
  app.post("/api/tools/screenshot", requireFamily, asyncHandler(takeScreenshot));
  app.get("/api/tools/screenshots", asyncHandler(getRecentScreenshots));
  app.get("/api/screenshot/:filename", validateParams({ filename: { string: { min: 1 }}}), asyncHandler(serveScreenshot));
  
  // 工作区文件
  app.get("/api/workspace/files", asyncHandler(getWorkspaceFiles));
  app.get("/api/workspace/file", validateQuery(["path"]), asyncHandler(readWorkspaceFile));
  
  // 编码搭档
  app.get("/api/coding/agents", asyncHandler(getCodingAgents));
  app.post("/api/coding/run", requireFamily, validateBody(["goal", "agentKey", "?cwd"]), asyncHandler(runCoding));
  app.get("/api/coding/runs/:id/events", validateParams({ id: { string: { min: 1 }}}), asyncHandler(getCodingEvents));
  app.post("/api/coding/runs/:id/stop", validateParams({ id: { string: { min: 1 }}}), asyncHandler(stopCoding));
  app.get("/api/coding/runs", asyncHandler(getCodingRuns));
  
  // 笔记
  app.get("/api/notes", validateQuery(["?q", "?owner", "?visibility", "?limit"]), asyncHandler(getNotes));
  app.get("/api/notes/export", asyncHandler(exportNotes));
  app.post("/api/notes/import", validateBody(["notes"]), asyncHandler(importNotes));
  app.get("/api/notes/:id", validateParams({ id: { string: { min: 1 }}}), asyncHandler(getNote));
  app.post("/api/notes", requireFamily, validateBody(["title", "?content_md"]), asyncHandler(createNote));
  app.patch("/api/notes/:id", validateParams({ id: { string: { min: 1 }}}), asyncHandler(updateNote));
  app.delete("/api/notes/:id", validateParams({ id: { string: { min: 1 }}}), asyncHandler(deleteNote));
  
  // AI 工作日志
  app.get("/api/journal", validateQuery(["?task_id", "?session_id", "?limit"]), asyncHandler(getJournal));
  app.post("/api/journal", asyncHandler(writeJournal));
}

export function registerSettingsRoutes(app, handlers) {
  const {
    getSettings,
    saveToolsSettings,
    savePreferences,
    savePricing,
    saveModelRoutes,
    saveFamilySettings,
    saveBackupSettings
  } = handlers;

  app.get("/api/settings", asyncHandler(getSettings));
  app.post("/api/settings/tools", requireFamily, validateBody(["?fileTools", "?terminal", "?gui", "?screenCapture", "?sandbox"]), asyncHandler(saveToolsSettings));
  app.post("/api/settings/preferences", requireFamily, validateBody(["?intelligenceLevel", "?maxAgents", "?defaultMode", "?defaultWorkDir"]), asyncHandler(savePreferences));
  app.post("/api/settings/pricing", requireFamily, validateBody(["?input", "?output", "?cached", "?currency"]), asyncHandler(savePricing));
  app.post("/api/settings/model-routes", requireFamily, validateBody(["?default", "?quick", "?translation", "?vision", "?coding"]), asyncHandler(saveModelRoutes));
  app.post("/api/settings/family", requireFamily, validateBody(["?enabled", "?password"]), asyncHandler(saveFamilySettings));
  app.post("/api/settings/backup", requireFamily, validateBody(["auto"]), asyncHandler(saveBackupSettings));
}

export function registerOpenRoutes(app, handlers) {
  const {
    getOpenApiConfig,
    saveOpenApiConfig,
    testWebhook,
    openHealth,
    openChat,
    createOpenTask,
    getOpenTask
  } = handlers;

  app.get("/api/open/config", asyncHandler(getOpenApiConfig));
  app.post("/api/open/config", requireAdmin, asyncHandler(saveOpenApiConfig));
  app.post("/api/open/webhook/test", requireAdmin, asyncHandler(testWebhook));
  app.get("/api/open/health", asyncHandler(openHealth));
  app.post("/api/open/chat", requireOpenApiKey, validateBody(["message"]), asyncHandler(openChat));
  app.post("/api/open/tasks", requireOpenApiKey, validateBody(["goal", "sessionId"]), asyncHandler(createOpenTask));
  app.get("/api/open/tasks/:id", requireOpenApiKey, validateParams({ id: { string: { min: 1 }}}), asyncHandler(getOpenTask));
}