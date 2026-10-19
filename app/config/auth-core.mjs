// 认证原语单一真源：无依赖，被 auth.mjs / server.mjs / handlers 共同引用
// 目的：切断 auth.mjs ↔ server.mjs 的循环依赖

import crypto from "crypto";

// ---------- 家庭访问口令 ----------

export function getFamilyPassword() {
  return String(process.env.FAMILY_ACCESS_PASSWORD || "").trim();
}

export function familySessionToken(password) {
  return crypto.createHmac("sha256", password).update("ai-temple-family-session").digest("hex");
}

export function parseCookies(req) {
  const out = {};
  const raw = String(req?.headers?.cookie || "");
  for (const part of raw.split(";")) {
    const idx = part.indexOf("=");
    if (idx < 0) continue;
    out[part.slice(0, idx).trim()] = decodeURIComponent(part.slice(idx + 1).trim());
  }
  return out;
}

export function isFamilyAuthenticated(req) {
  const password = getFamilyPassword();
  if (!password) return true;
  const cookie = parseCookies(req).family_session || "";
  const expected = familySessionToken(password);
  if (!cookie || cookie.length !== expected.length) return false;
  try {
    return crypto.timingSafeEqual(Buffer.from(cookie), Buffer.from(expected));
  } catch {
    return false;
  }
}

// ---------- 绑定主机 / 回环判定 ----------

export function getBindHost() {
  return String(process.env.BIND_HOST || "127.0.0.1").trim();
}

export function isLoopbackHost(host) {
  const h = String(host || "").trim().toLowerCase().replace(/^\[|\]$/g, "");
  return h === "127.0.0.1" || h === "localhost" || h === "::1" || h === "0:0:0:0:0:0:0:1" || h === "";
}

export function isLoopbackRequest(req) {
  const addr = String(req?.socket?.remoteAddress || "");
  return addr === "127.0.0.1" || addr === "::1" || addr === "::ffff:127.0.0.1";
}

// ---------- 管理后台鉴权 ----------

export function adminAuthRequired() {
  const explicit = String(process.env.ADMIN_AUTH || process.env.REQUIRE_ADMIN_AUTH || "auto")
    .trim().toLowerCase();
  if (["1", "on", "true", "yes", "required", "enable", "enabled"].includes(explicit)) return true;
  if (["0", "off", "false", "no", "none", "disable", "disabled"].includes(explicit)) return false;
  return !isLoopbackHost(getBindHost());
}

export function adminToken(password = String(process.env.ADMIN_PASSWORD || "")) {
  return crypto.createHmac("sha256", "agent-squad-admin-session-v1").update(password).digest("hex");
}

export function isAdminAuthed(req) {
  if (!adminAuthRequired()) return true;
  const expected = adminToken();
  if (parseCookies(req).admin_token === expected) return true;
  const auth = String(req?.headers?.authorization || "");
  if (auth.toLowerCase().startsWith("bearer ")) {
    const token = auth.slice(7).trim();
    if (token && token === expected) return true;
  }
  return false;
}

export function adminCookieHeader() {
  return `admin_token=${encodeURIComponent(adminToken())}; Path=/; HttpOnly; SameSite=Strict; Max-Age=86400`;
}

// ---------- 免鉴权路径判定（server.mjs 与 auth.mjs 共用同一套规则） ----------

export function isFamilyExemptPath(p) {
  return p === "/api/health" || p.startsWith("/api/family/");
}

export function isDesktopExemptPath(p) {
  return (
    p === "/api/desktop/notifications" ||
    p === "/api/desktop/selection" ||
    p === "/api/desktop/selection/ack"
  );
}

export function isAdminExemptPath(p) {
  return (
    p.startsWith("/api/admin/") ||
    p === "/api/check-login" ||
    p === "/api/save-env-config" ||
    p === "/api/config-permission"
  );
}