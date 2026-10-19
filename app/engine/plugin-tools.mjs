// 插件声明式工具
// 插件 config.tools 声明工具，启用后真实注册进模型工具列表：
// {
//   name: "query_order", description: "...",
//   readOnly: true,
//   parameters: { type:"object", properties:{ id:{type:"string"} }, required:["id"] },
//   execution: { type:"http", url:"https://...", method:"POST", headers:{}, bodyTemplate:"{\"id\":\"{{id}}\"}" }
//   或 { type:"command", command:"python", args:["tool.py","{{id}}"], timeoutMs:20000 }
// }
import fs from "node:fs";
import path from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

function sanitize(name) {
  const cleaned = String(name || "")
    .toLowerCase()
    .replace(/[^a-z0-9_-]/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 28);
  return /[a-z0-9]/.test(cleaned) ? cleaned : "tool";
}

function parseConfig(v) {
  if (v && typeof v === "object") return v;
  try { return v ? JSON.parse(v) : {}; } catch { return {}; }
}

function fillTemplate(text, args) {
  return String(text ?? "").replace(/\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}/g, (_, key) => {
    const v = args?.[key];
    return v === undefined || v === null ? "" : String(v);
  });
}

// 构建已启用插件的工具定义与索引
export function buildPluginTools(db) {
  const openai = [];
  const claude = [];
  const index = new Map();
  const readOnlyNames = [];
  let rows = [];
  try {
    rows = db.prepare("SELECT * FROM store_items WHERE kind = 'plugin' AND enabled = 1").all();
  } catch {
    return { openai, claude, index, readOnlyNames, count: 0 };
  }
  for (const row of rows) {
    const cfg = parseConfig(row.config);
    const tools = Array.isArray(cfg.tools) ? cfg.tools : [];
    for (const tool of tools) {
      if (!tool || !tool.name) continue;
      let name = `plugin_${sanitize(row.name)}_${sanitize(tool.name)}`;
      let n = 1;
      while (index.has(name)) name = `plugin_${sanitize(row.name)}_${sanitize(tool.name)}_${n++}`;
      const schema = tool.parameters && typeof tool.parameters === "object"
        ? tool.parameters
        : { type: "object", properties: {} };
      const description = `[插件:${row.name}] ${tool.description || tool.name}`;
      openai.push({ type: "function", function: { name, description, parameters: schema } });
      claude.push({ name, description, input_schema: schema });
      index.set(name, { pluginId: row.id, pluginName: row.name, tool });
      if (tool.readOnly) readOnlyNames.push(name);
    }
  }
  return { openai, claude, index, readOnlyNames, count: openai.length };
}

// 执行插件工具
export async function callPluginTool(pluginTool, args = {}, context = {}) {
  const tool = pluginTool?.tool || {};
  const exec = tool.execution || {};
  const timeoutMs = Math.min(Math.max(Number(exec.timeoutMs) || 20000, 1000), 60000);

  if (exec.type === "http") {
    const url = fillTemplate(exec.url || "", args);
    if (!/^https?:\/\//i.test(url)) return { error: true, message: `插件工具 URL 不合法: ${url}` };
    const method = String(exec.method || "POST").toUpperCase();
    const headers = { ...(exec.headers || {}) };
    let body;
    if (method !== "GET" && method !== "HEAD") {
      if (exec.bodyTemplate) {
        body = fillTemplate(exec.bodyTemplate, args);
        if (!headers["Content-Type"] && !headers["content-type"]) headers["Content-Type"] = "application/json";
      } else {
        body = JSON.stringify(args);
        headers["Content-Type"] = "application/json";
      }
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const res = await fetch(url, { method, headers, body, signal: controller.signal });
      const text = await res.text();
      return {
        success: res.ok,
        status: res.status,
        content: text.slice(0, 8000),
        plugin: pluginTool.pluginName,
        tool: tool.name
      };
    } catch (e) {
      return { error: true, message: `插件 HTTP 调用失败: ${e?.message || e}` };
    } finally {
      clearTimeout(timer);
    }
  }

  if (exec.type === "command") {
    const command = fillTemplate(exec.command || "", args);
    if (!command) return { error: true, message: "插件命令为空" };
    const argv = (Array.isArray(exec.args) ? exec.args : []).map((a) => fillTemplate(a, args));
    const cwd = context.sandboxRoot && fs.existsSync(context.sandboxRoot)
      ? context.sandboxRoot
      : (context.workspaceRoot || context.cwd || process.cwd());
    // 危险命令护栏
    const joined = `${command} ${argv.join(" ")}`.toLowerCase();
    const blocked = ["rm -rf /", "format ", "diskpart", "shutdown", "reboot", "reg delete", "net user"];
    if (blocked.some((b) => joined.includes(b))) {
      return { error: true, message: "插件命令被安全策略拦截" };
    }
    try {
      const { stdout, stderr } = await execFileAsync(command, argv, {
        cwd,
        timeout: timeoutMs,
        maxBuffer: 10 * 1024 * 1024,
        windowsHide: true
      });
      return {
        success: true,
        stdout: String(stdout || "").slice(0, 8000),
        stderr: String(stderr || "").slice(0, 2000),
        plugin: pluginTool.pluginName,
        tool: tool.name,
        sandboxed: Boolean(context.sandboxRoot)
      };
    } catch (e) {
      return { error: true, message: `插件命令执行失败: ${e?.message || e}` };
    }
  }

  return { error: true, message: "插件工具未配置 execution.type（http / command）" };
}

export default { buildPluginTools, callPluginTool };
