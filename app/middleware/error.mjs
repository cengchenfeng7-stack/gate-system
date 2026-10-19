// 全局错误处理中间件

import { ERROR_CODES, HTTP_STATUS } from "../config/constants.mjs";

export function errorHandler(err, req, res, next) {
  console.error(`[Error] ${req.method} ${req.path}:`, err?.stack || err);
  
  // 已知错误类型
  if (err?.code && ERROR_CODES[err.code]) {
    const statusMap = {
      VALIDATION_ERROR: HTTP_STATUS.BAD_REQUEST,
      UNAUTHORIZED: HTTP_STATUS.UNAUTHORIZED,
      FORBIDDEN: HTTP_STATUS.FORBIDDEN,
      NOT_FOUND: HTTP_STATUS.NOT_FOUND,
      FAMILY_AUTH_REQUIRED: HTTP_STATUS.UNAUTHORIZED,
      ADMIN_AUTH_REQUIRED: HTTP_STATUS.UNAUTHORIZED,
      OPEN_API_DISABLED: HTTP_STATUS.FORBIDDEN,
      INVALID_API_KEY: HTTP_STATUS.UNAUTHORIZED,
      INSUFFICIENT_PERMISSION: HTTP_STATUS.FORBIDDEN,
      RATE_LIMITED: HTTP_STATUS.TOO_MANY_REQUESTS,
      PROVIDER_NOT_CONFIGURED: HTTP_STATUS.SERVICE_UNAVAILABLE,
      MODEL_UNAVAILABLE: HTTP_STATUS.SERVICE_UNAVAILABLE,
      TOOL_EXECUTION_FAILED: HTTP_STATUS.INTERNAL_SERVER_ERROR,
      PERMISSION_DENIED: HTTP_STATUS.FORBIDDEN,
      PATH_TRAVERSAL: HTTP_STATUS.FORBIDDEN,
      SANDBOX_VIOLATION: HTTP_STATUS.FORBIDDEN,
      ORACLE_BLACKLIST: HTTP_STATUS.FORBIDDEN,
      ORACLE_ARBITRATION_REQUIRED: HTTP_STATUS.FORBIDDEN
    };
    return res.status(statusMap[err.code] || HTTP_STATUS.INTERNAL_SERVER_ERROR).json({
      error: err.message || "请求失败",
      code: err.code,
      details: err.details
    });
  }
  
  // 数据库错误
  if (err?.code === "SQLITE_CONSTRAINT" || err?.code === "SQLITE_BUSY") {
    return res.status(HTTP_STATUS.CONFLICT).json({
      error: "数据冲突或繁忙，请重试",
      code: "DATABASE_ERROR"
    });
  }
  
  // JSON 解析错误
  if (err instanceof SyntaxError && err.status === 400 && "body" in err) {
    return res.status(HTTP_STATUS.BAD_REQUEST).json({
      error: "JSON 格式错误",
      code: "INVALID_JSON"
    });
  }
  
  // 默认 500
  const message = process.env.NODE_ENV === "production" 
    ? "服务器内部错误" 
    : (err?.message || "未知错误");
  
  res.status(HTTP_STATUS.INTERNAL_SERVER_ERROR).json({
    error: message,
    code: "INTERNAL_ERROR",
    ...(process.env.NODE_ENV !== "production" && { stack: err?.stack })
  });
}

// 404 处理
export function notFoundHandler(req, res) {
  res.status(404).json({
    error: `接口不存在: ${req.method} ${req.path}`,
    code: "NOT_FOUND"
  });
}

// 异步路由包装器（自动捕获 async 错误）
export function asyncHandler(fn) {
  return (req, res, next) => {
    Promise.resolve(fn(req, res, next)).catch(next);
  };
}

// 请求日志中间件
export function requestLogger(req, res, next) {
  const start = Date.now();
  res.on("finish", () => {
    const duration = Date.now() - start;
    const logData = {
      method: req.method,
      path: req.path,
      status: res.statusCode,
      durationMs: duration,
      ip: req.ip || req.socket?.remoteAddress,
      userAgent: req.headers["user-agent"]
    };
    if (res.statusCode >= 400) {
      console.warn(`[Request] ${JSON.stringify(logData)}`);
    } else if (duration > 1000) {
      console.log(`[Request] Slow: ${JSON.stringify(logData)}`);
    }
  });
  next();
}