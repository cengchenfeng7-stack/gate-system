// 编码搭档：代理探测、命令构造、环境变量、进程管理
// server.mjs 与 routes/tools/handlers.mjs 共用

import { spawn, execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

// ---------- 编码代理登记表 ----------

const CODING_AGENTS = [
  {
    key: "claude",
    name: "Claude Code",
    template: "claude -p {goal}",
    detect: { bin: "claude", hint: "npm i -g @anthropic-ai/claude-code" }
  },
  {
    key: "codex",
    name: "Codex CLI",
    template: "codex exec {goal}",
    detect: { bin: "codex", hint: "npm i -g @openai/codex" }
  },
  {
    key: "gemini",
    name: "Gemini CLI",
    template: "gemini -p {goal}",
    detect: { bin: "gemini", hint: "npm i -g @google/gemini-cli" }
  },
  {
    key: "aider",
    name: "Aider",
    template: "aider --message {goal} --yes",
    detect: { bin: "aider", hint: "pip install aider-chat" }
  },
  {
    key: "cursor",
    name: "Cursor Agent",
    template: "cursor-agent -p {goal}",
    detect: { bin: "cursor-agent", hint: "安装 Cursor CLI" }
  },
  {
    key: "opencode",
    name: "OpenCode",
    template: "opencode run {goal}",
    detect: { bin: "opencode", hint: "npm i -g opencode-ai" }
  }
];

// ---------- 命令探测（带缓存） ----------

let detectCache = null;

function whichSync(bin) {
  const cmd = process.platform === "win32" ? "where" : "which";
  try {
    const out = execFileSync(cmd, [bin], {
      windowsHide: true,
      timeout: 4000,
      encoding: "utf8"
    });
    const first = String(out).split(/\r?\n/).map((s) => s.trim()).filter(Boolean)[0];
    return first && fs.existsSync(first) ? first : null;
  } catch {
    return null;
  }
}

export function detectCodingAgents(forceRefresh = false) {
  if (detectCache && !forceRefresh) return detectCache;

  const results = [];
  for (const agent of CODING_AGENTS) {
    const binPath = whichSync(agent.detect.bin);
    results.push({
      key: agent.key,
      name: agent.name,
      available: Boolean(binPath),
      binPath,
      template: agent.template,
      hint: agent.detect.hint
    });
  }

  detectCache = results;
  return results;
}

export function findCodingAgent(key) {
  if (!key) return null;
  const wanted = String(key).trim();
  return detectCodingAgents().find((a) => a.key === wanted) || null;
}

// ---------- 命令构造 ----------

/**
 * Windows 下 cmd.exe 会解释 & | < > ^ 等字符，POSIX 用单引号即可。
 */
export function quoteShellArg(value, platform = process.platform) {
  const s = String(value ?? "");
  if (platform === "win32") {
    if (s === "") return '""';
    if (!/[\s"^&|<>()]/.test(s)) return s;
    return '"' + s.replace(/(\\*)"/g, '$1$1\\"').replace(/(\\*)$/, '$1$1') + '"';
  }
  if (s === "") return "''";
  if (/^[A-Za-z0-9_/:=@%+.,-]+$/.test(s)) return s;
  return "'" + s.replace(/'/g, "'\\''") + "'";
}

export function buildCodingCommand(template, goal, model) {
  let cmd = String(template || "").trim();
  if (!cmd) throw new Error("命令模板不能为空");

  // 支持 {goal} {model} 占位符；未含 {goal} 时把目标追加到末尾
  cmd = cmd.replace(/\{goal\}/g, quoteShellArg(goal));
  if (model) {
    cmd = cmd.replace(/\{model\}/g, quoteShellArg(model));
  }
  if (!/\{goal\}/.test(String(template))) {
    cmd = `${cmd} ${quoteShellArg(goal)}`;
  }
  return cmd;
}

// ---------- 工作目录解析 ----------

export function resolveCodingCwd(requested) {
  const workspaceRoot = path.resolve(process.cwd(), "..", "workspace");
  const input = String(requested || "").trim();

  const candidate = input
    ? path.resolve(workspaceRoot, input)
    : workspaceRoot;

  // 防目录穿越：必须落在 workspace 之内
  const rel = path.relative(workspaceRoot, candidate);
  if (rel.startsWith("..") || path.isAbsolute(rel)) {
    throw new Error("工作目录必须位于 workspace 内");
  }
  if (!fs.existsSync(candidate) || !fs.statSync(candidate).isDirectory()) {
    return null;
  }
  return candidate;
}

// ---------- 提供商环境变量 ----------

/**
 * 为编码 CLI 注入模型提供商的 API Key / Base URL，
 * 兼容 Claude Code / Codex / Gemini / Aider 等常见变量名。
 */
export function getCodingProviderEnv(model) {
  const env = { ...process.env };

  const direct = readDirectProvider();
  const openaiKey = firstEnv("OPENAI_API_KEY");
  const openaiBase = firstEnv("OPENAI_BASE_URL", "OPENAI_API_BASE");

  // Claude Code
  if (direct?.apiKey) env.ANTHROPIC_API_KEY = direct.apiKey;
  if (direct?.baseUrl) env.ANTHROPIC_BASE_URL = direct.baseUrl;
  if (direct?.model || model) env.ANTHROPIC_MODEL = direct?.model || model;

  // Codex / OpenAI 兼容
  const codexKey = direct?.apiKey || openaiKey;
  const codexBase = direct?.baseUrl || openaiBase;
  if (codexKey) env.OPENAI_API_KEY = codexKey;
  if (codexBase) env.OPENAI_BASE_URL = codexBase;
  if (model) env.CODING_MODEL = model;

  // Aider
  if (codexKey) env.OPENAI_API_KEY = codexKey;

  env.CODING_MODEL = model || env.CODING_MODEL || "";
  return env;
}

function firstEnv(...keys) {
  for (const k of keys) {
    const v = String(process.env[k] || "").trim();
    if (v) return v;
  }
  return "";
}

function readDirectProvider() {
  try {
    const preset = JSON.parse(process.env.DIRECT_PROVIDER_PRESET || "{}");
    if (preset && (preset.apiKey || preset.baseUrl)) {
      return { apiKey: preset.apiKey || "", baseUrl: preset.baseUrl || "", model: preset.model || "" };
    }
  } catch {
    /* 解析失败则回落到环境变量 */
  }
  return {
    apiKey: firstEnv("ANTHROPIC_API_KEY", "OPENAI_API_KEY"),
    baseUrl: firstEnv("ANTHROPIC_BASE_URL", "OPENAI_BASE_URL"),
    model: firstEnv("ANTHROPIC_MODEL", "OPENAI_MODEL")
  };
}

export { spawn };