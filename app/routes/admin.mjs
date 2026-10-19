// 管理后台/桌面壳路由

import { asyncHandler } from "../middleware/error.mjs";
import { validateBody, validateParams, validateQuery } from "../middleware/validation.mjs";
import { requireAdmin, requireFamily } from "../middleware/auth.mjs";

export function registerAdminRoutes(app, handlers) {
  const {
    getAdminAuth,
    adminLogin,
    adminLogout,
    getAdminOverview,
    getAdminSessions,
    deleteAdminSession,
    getAdminMessages,
    getAdminIntents,
    exportAdminChat,
    checkLogin,
    saveEnvConfig,
    getConfigPermission,
    setConfigPermission
  } = handlers;

  app.get("/api/admin/auth", asyncHandler(getAdminAuth));
  app.post("/api/admin/login", asyncHandler(adminLogin));
  app.post("/api/admin/logout", asyncHandler(adminLogout));
  app.get("/api/admin/overview", asyncHandler(getAdminOverview));
  app.get("/api/admin/sessions", asyncHandler(getAdminSessions));
  app.delete("/api/admin/sessions/:id", validateParams({ id: { string: { min: 1 }}}), asyncHandler(deleteAdminSession));
  app.get("/api/admin/messages", asyncHandler(getAdminMessages));
  app.get("/api/admin/intents", asyncHandler(getAdminIntents));
  app.get("/api/admin/export", validateQuery(["?sessionId"]), asyncHandler(exportAdminChat));
  app.get("/api/check-login", asyncHandler(checkLogin));
  app.post("/api/save-env-config", requireAdmin, asyncHandler(saveEnvConfig));
  app.get("/api/config-permission", asyncHandler(getConfigPermission));
  app.post("/api/config-permission", requireAdmin, validateBody(["?level"]), asyncHandler(setConfigPermission));
}

export function registerDesktopRoutes(app, handlers) {
  const {
    getDesktopNotifications,
    postDesktopSelection,
    getDesktopSelection,
    ackDesktopSelection
  } = handlers;

  // 桌面壳专用接口 (仅本机访问)
  app.get("/api/desktop/notifications", asyncHandler(getDesktopNotifications));
  app.post("/api/desktop/selection", asyncHandler(postDesktopSelection));
  app.get("/api/desktop/selection", asyncHandler(getDesktopSelection));
  app.post("/api/desktop/selection/ack", asyncHandler(ackDesktopSelection));
}