// 统一参数校验中间件

import { ERROR_CODES, HTTP_STATUS } from "../config/constants.mjs";

/**
 * 归一化字段声明。兼容三种写法：
 *   ["a", "b"]                 → a、b 都必填
 *   ["a", "?b"]                → a 必填，b 可选
 *   { a: { string: { min: 1 } } } → a 必填 + 规则校验
 *   { a: "?", b: {} }          → a 可选，b 必填
 */
function normalizeFields(spec) {
  const out = [];

  if (Array.isArray(spec)) {
    for (const f of spec) {
      const s = String(f).trim();
      if (s.startsWith("?")) out.push({ name: s.slice(1), required: false, rules: null });
      else out.push({ name: s, required: true, rules: null });
    }
    return out;
  }

  if (spec && typeof spec === "object") {
    for (const [name, rules] of Object.entries(spec)) {
      const value = rules === "?" ? "?" : rules;
      const isOptional = value === "?" || (value && typeof value === "object" && value.optional === true);
      const realRules = value === "?" ? null
        : (value && typeof value === "object" ? value : null);
      out.push({
        name,
        required: !isOptional,
        rules: realRules && typeof realRules === "object" ? realRules : null
      });
    }
    return out;
  }

  return out;
}

/** 从 req 的指定位置取值 */
function pick(req, field) {
  if (req.params && field in req.params) return { found: true, value: req.params[field] };
  if (req.query && field in req.query) return { found: true, value: req.query[field] };
  if (req.body && field in req.body) return { found: true, value: req.body[field] };
  return { found: false, value: undefined };
}

/** 空值判定：undefined / null / 空串 / 空数组 */
function isEmpty(v) {
  if (v === undefined || v === null) return true;
  if (typeof v === "string") return v.trim() === "";
  if (Array.isArray(v)) return v.length === 0;
  if (typeof v === "object") return Object.keys(v).length === 0;
  return false;
}

function runRules(field, value, rules) {
  if (!rules || typeof rules !== "object") return null;
  const errors = [];

  for (const [validatorName, options] of Object.entries(rules)) {
    const validator = validators[validatorName];
    if (!validator) continue;
    const opts = options === true || options === undefined ? {} : options;
    const err = validator(value, opts);
    if (err) errors.push(err);
  }

  return errors.length ? `${field}: ${errors.join("；")}` : null;
}

// ==================== Body 校验 ====================

export function validateBody(fields = []) {
  const spec = normalizeFields(fields);
  return (req, res, next) => {
    const missing = [];
    const invalid = [];

    for (const f of spec) {
      const { found, value } = pick(req, f.name);
      const source = req.body && f.name in req.body ? req.body : req.query;

      if (!found || isEmpty(value)) {
        if (f.required) missing.push(f.name);
        continue;
      }

      if (f.rules) {
        const err = runRules(f.name, value, f.rules);
        if (err) invalid.push(err);
      }
      void source;
    }

    if (missing.length) {
      return res.status(HTTP_STATUS.BAD_REQUEST).json({
        error: `缺少必填字段: ${missing.join(", ")}`,
        code: ERROR_CODES.VALIDATION_ERROR,
        fields: missing
      });
    }
    if (invalid.length) {
      return res.status(HTTP_STATUS.BAD_REQUEST).json({
        error: "参数校验失败",
        code: ERROR_CODES.VALIDATION_ERROR,
        details: invalid
      });
    }
    next();
  };
}

// ==================== Query 校验 ====================

export function validateQuery(fields = []) {
  const spec = normalizeFields(fields);
  return (req, res, next) => {
    const missing = [];
    const invalid = [];

    for (const f of spec) {
      const { found, value } = pick(req, f.name);
      if (!found || isEmpty(value)) {
        if (f.required) missing.push(f.name);
        continue;
      }
      if (f.rules) {
        const err = runRules(f.name, value, f.rules);
        if (err) invalid.push(err);
      }
    }

    if (missing.length) {
      return res.status(HTTP_STATUS.BAD_REQUEST).json({
        error: `缺少查询参数: ${missing.join(", ")}`,
        code: ERROR_CODES.VALIDATION_ERROR,
        fields: missing
      });
    }
    if (invalid.length) {
      return res.status(HTTP_STATUS.BAD_REQUEST).json({
        error: "参数校验失败",
        code: ERROR_CODES.VALIDATION_ERROR,
        details: invalid
      });
    }
    next();
  };
}

// ==================== Params 校验 ====================

export function validateParams(fields = []) {
  const spec = normalizeFields(fields);
  return (req, res, next) => {
    const missing = [];
    const invalid = [];

    for (const f of spec) {
      const value = req.params ? req.params[f.name] : undefined;
      if (value === undefined || value === null || value === "") {
        if (f.required) missing.push(f.name);
        continue;
      }
      if (f.rules) {
        const err = runRules(f.name, value, f.rules);
        if (err) invalid.push(err);
      }
    }

    if (missing.length) {
      return res.status(HTTP_STATUS.BAD_REQUEST).json({
        error: `缺少路径参数: ${missing.join(", ")}`,
        code: ERROR_CODES.VALIDATION_ERROR,
        fields: missing
      });
    }
    if (invalid.length) {
      return res.status(HTTP_STATUS.BAD_REQUEST).json({
        error: "参数校验失败",
        code: ERROR_CODES.VALIDATION_ERROR,
        details: invalid
      });
    }
    next();
  };
}

// ==================== 内置校验器 ====================

export const validators = {
  string: (value, { min = 0, max = Infinity, pattern } = {}) => {
    if (typeof value !== "string") return "必须是字符串";
    if (value.length < min) return `长度不能少于 ${min}`;
    if (value.length > max) return `长度不能超过 ${max}`;
    if (pattern && !pattern.test(value)) return "格式不正确";
    return null;
  },
  number: (value, { min = -Infinity, max = Infinity, integer = false } = {}) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return "必须是数字";
    if (integer && !Number.isInteger(n)) return "必须是整数";
    if (n < min) return `不能小于 ${min}`;
    if (n > max) return `不能大于 ${max}`;
    return null;
  },
  boolean: (value) => (typeof value === "boolean" ? null : "必须是布尔值"),
  array: (value, { min = 0, max = Infinity, itemType } = {}) => {
    if (!Array.isArray(value)) return "必须是数组";
    if (value.length < min) return `数组长度不能少于 ${min}`;
    if (value.length > max) return `数组长度不能超过 ${max}`;
    if (itemType && validators[itemType]) {
      for (const item of value) {
        const err = validators[itemType](item);
        if (err) return `数组项 ${err}`;
      }
    }
    return null;
  },
  object: (value, { requiredFields = [] } = {}) => {
    if (!value || typeof value !== "object" || Array.isArray(value)) return "必须是对象";
    const missing = requiredFields.filter((f) => value[f] === undefined);
    if (missing.length) return `缺少字段: ${missing.join(", ")}`;
    return null;
  },
  uuid: (value) =>
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(String(value))
      ? null : "必须是有效的 UUID",
  email: (value) =>
    /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(value)) ? null : "必须是有效的邮箱",
  url: (value) => (/^https?:\/\//i.test(String(value)) ? null : "必须是有效的 URL"),
  date: (value) => (isNaN(new Date(value).getTime()) ? "必须是有效的日期时间" : null),
  enum: (value, { values = [] } = {}) =>
    values.includes(value) ? null : `必须是以下之一: ${values.join(", ")}`
};

// ==================== 通用 schema 校验器 ====================

export function buildValidator(schema) {
  return (req, res, next) => {
    const errors = [];
    for (const [field, rules] of Object.entries(schema || {})) {
      const value = req.body?.[field];
      const err = runRules(field, value, rules);
      if (err) errors.push(err);
    }
    if (errors.length) {
      return res.status(HTTP_STATUS.BAD_REQUEST).json({
        error: "参数校验失败",
        code: ERROR_CODES.VALIDATION_ERROR,
        details: errors
      });
    }
    next();
  };
}

// ==================== 常用校验模式 ====================

export const commonSchemas = {
  pagination: {
    limit: { number: { min: 1, max: 500, integer: true } },
    offset: { number: { min: 0, integer: true } }
  },
  idParam: {
    id: { string: { pattern: /^[a-zA-Z0-9_-]+$/ } }
  },
  uuidParam: {
    id: { uuid: {} }
  },
  sessionId: {
    sessionId: { string: { min: 1 } }
  },
  message: {
    message: { string: { min: 1, max: 20000 } }
  }
};