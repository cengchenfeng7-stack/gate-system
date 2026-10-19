// 统一认证中间件
// 认证原语来自 config/auth-core.mjs（单一真源，无循环依赖）
// 开放 API 的存储实现由 server.mjs 通过 setOpenApiDeps() 注入

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
} from "../config/auth-core.mjs";

// 重新导出认证原语，供 server.mjs 与各 handler 统一引用
export {
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
};

// ---------- 开放 API 依赖注入（由 server.mjs 在启动时调用） ----------

let openApiDeps = null;

/**
 * 注入开放 API 相关实现，避免 auth.mjs 直接依赖 server.mjs
 * @param {{ getOpenApiConfig: Function, getOpenApiKeys: Function, setOpenApiKeys: Function,
 *           findApiKey: Function, hasPermission: Function, sendWebhook: Function }} deps
 */
export function setOpenApiDeps(deps) {
  openApiDeps = deps;
}

function requireDeps(name) {
  if (!openApiDeps || typeof openApiDeps[name] !== "function") {
    throw new Error(`auth.mjs: 开放 API 依赖未注入（缺少 ${name}），请检查 server.mjs 是否调用 setOpenApiDeps()`);
  }
  return openApiDeps[name];
}

// 便于测试/直接调用的包装
export function getOpenApiConfig() { return requireDeps("getOpenApiConfig")(); }
export function getOpenApiKeys() { return requireDeps("getOpenApiKeys")(); }
export function setOpenApiKeys(keys) { return requireDeps("setOpenApiKeys")(keys); }
export function findApiKey(provided) { return requireDeps("findApiKey")(provided); }
export function hasApiKeyPermission(keyObj, req) { return requireDeps("hasPermission")(keyObj, req); }
export function sendWebhook(event, payload) { return requireDeps("sendWebhook")(event, payload); }

// ---------- 鉴权中间件 ----------

// 家庭访问口令：桌面壳从本机轮询通知 / 投递全局划词时不拦截
export function requireFamily(req, res, next) {
  const password = getFamilyPassword();
  if (!password) return next();

  const p = req.path;
  if (isFamilyExemptPath(p)) return next();
  if (isDesktopExemptPath(p) && isLoopbackRequest(req)) return next();
  if (isAdminExemptPath(p)) return next();

  if (isFamilyAuthenticated(req)) return next();
  return res.status(401).json({ error: "需要家庭访问口令", code: "FAMILY_AUTH_REQUIRED" });
}

// 管理后台鉴权
export function requireAdmin(req, res, next) {
  if (!adminAuthRequired()) return next();
  if (isAdminAuthed(req)) return next();
  return res.status(401).json({ error: "未授权：请先打开 /admin 输入管理密码登录" });
}

// 开放 API Key 鉴权
export function requireOpenApiKey(req, res, next) {
  let cfg;
  try {
    cfg = requireDeps("getOpenApiConfig")();
  } catch (e) {
    return next(e);
  }
  if (!cfg?.enabled) return res.status(403).json({ error: "开放 API 未启用", code: "OPEN_API_DISABLED" });

  const provided = String(req.headers["x-api-key"] || "").trim();
  if (!provided) {
    return res.status(401).json({ error: "缺少 X-API-Key", code: "INVALID_API_KEY" });
  }

  const keyObj = requireDeps("findApiKey")(provided);
  if (!keyObj) {
    return res.status(401).json({ error: "API Key 无效", code: "INVALID_API_KEY" });
  }

  if (!requireDeps("hasPermission")(keyObj, req)) {
    return res.status(403).json({
      error: "权限不足",
      code: "INSUFFICIENT_PERMISSION",
      required: keyObj.permissions
    });
  }

  req.openApiKey = keyObj;

  // 更新最后使用时间（失败不影响主流程）
  try {
    const keys = requireDeps("getOpenApiKeys")();
    const idx = keys.findIndex((k) => k.id === keyObj.id);
    if (idx >= 0) {
      keys[idx].lastUsed = new Date().toISOString();
      requireDeps("setOpenApiKeys")(keys);
    }
  } catch {
    /* 忽略 */
  }

  next();
}