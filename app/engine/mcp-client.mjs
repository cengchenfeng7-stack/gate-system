// MCP 运行时客户端
// 真正启动 stdio / SSE / HTTP 传输，拉取工具并转发调用
// 工具命名：mcp_<server>_<tool>，由 providers 注入到模型工具列表

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { SSEClientTransport } from "@modelcontextprotocol/sdk/client/sse.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

function sanitize(name) {
  const cleaned = String(name || "server")
    .toLowerCase()
    .replace(/[^a-z0-9_-]/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 32);
  return /[a-z0-9]/.test(cleaned) ? cleaned : "srv";
}

function parseConfig(v) {
  if (v && typeof v === "object") return v;
  try { return v ? JSON.parse(v) : {}; } catch { return {}; }
}

function withTimeout(promise, ms, label) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${label} 超时（${ms}ms）`)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

export class McpManager {
  constructor(db) {
    this.db = db;
    this.connections = new Map(); // itemId -> { client, transport, name, tools: [], error }
    this.toolIndex = new Map();   // namespacedName -> { itemId, originalName }
    this.prefixIndex = new Map(); // itemId -> namespaced prefix
  }

  listConfigs() {
    return this.db.prepare("SELECT * FROM store_items WHERE kind = 'mcp'").all()
      .map((r) => ({ ...r, config: parseConfig(r.config) }));
  }

  async connect(item, { timeoutMs = 20000 } = {}) {
    const cfg = item.config || {};
    if (this.connections.has(item.id)) return this.statusOf(item.id);
    let transport;
    try {
      if (cfg.url) {
        const url = new URL(cfg.url);
        const transportType = String(cfg.transport || "sse").toLowerCase();
        transport = transportType === "http"
          ? new StreamableHTTPClientTransport(url)
          : new SSEClientTransport(url);
      } else {
        if (!cfg.command) throw new Error("未配置 command 或 url");
        let command = String(cfg.command);
        let args = Array.isArray(cfg.args) ? cfg.args.map(String) : [];
        // Windows 下 npx/npm/pnpm/yarn 需要通过 cmd.exe 启动
        if (process.platform === "win32" && ["npx", "npm", "pnpm", "yarn"].includes(command.toLowerCase())) {
          args = ["/c", command, ...args];
          command = "cmd.exe";
        }
        const env = {};
        for (const [k, v] of Object.entries({ ...process.env, ...(cfg.env || {}) })) {
          if (typeof v === "string") env[k] = v;
        }
        transport = new StdioClientTransport({
          command,
          args,
          env,
          cwd: cfg.cwd || undefined,
          stderr: "pipe"
        });
      }

      const client = new Client(
        { name: "ai-temple", version: "1.0.0" },
        { capabilities: {} }
      );
      await withTimeout(client.connect(transport), timeoutMs, `MCP「${item.name}」连接`);
      const listed = await withTimeout(client.listTools(), timeoutMs, `MCP「${item.name}」拉取工具`);
      const tools = Array.isArray(listed?.tools) ? listed.tools : [];
      this.connections.set(item.id, { client, transport, name: item.name, tools, error: null });
      this.rebuildIndex();
      console.log(`[MCP] 已连接「${item.name}」，工具 ${tools.length} 个`);
      return this.statusOf(item.id);
    } catch (e) {
      try { await transport?.close?.(); } catch { /* 忽略 */ }
      this.connections.set(item.id, { client: null, transport: null, name: item.name, tools: [], error: e?.message || String(e) });
      console.warn(`[MCP] 连接「${item.name}」失败:`, e?.message || e);
      return this.statusOf(item.id);
    }
  }

  async disconnect(itemId) {
    const conn = this.connections.get(itemId);
    if (conn) {
      try { await conn.client?.close?.(); } catch { /* 忽略 */ }
      try { await conn.transport?.close?.(); } catch { /* 忽略 */ }
      this.connections.delete(itemId);
      this.rebuildIndex();
    }
    return { success: true };
  }

  async disconnectAll() {
    for (const id of [...this.connections.keys()]) await this.disconnect(id);
  }

  rebuildIndex() {
    this.toolIndex.clear();
    this.prefixIndex.clear();
    for (const [id, conn] of this.connections.entries()) {
      if (!conn.client) continue;
      const prefix = `mcp_${sanitize(conn.name)}_`;
      this.prefixIndex.set(id, prefix);
      for (const t of conn.tools) {
        let candidate = `${prefix}${sanitize(t.name)}`;
        let n = 1;
        while (this.toolIndex.has(candidate)) {
          candidate = `${prefix}${sanitize(t.name)}_${n++}`;
        }
        this.toolIndex.set(candidate, { itemId: id, originalName: t.name });
      }
    }
  }

  // 已连接服务器的工具定义（OpenAI / Claude 双格式）
  getToolDefs(limit = 80) {
    const openai = [];
    const claude = [];
    for (const [namespaced, entry] of this.toolIndex.entries()) {
      if (openai.length >= limit) break;
      const conn = this.connections.get(entry.itemId);
      const tool = conn?.tools.find((t) => t.name === entry.originalName);
      if (!tool) continue;
      const schema = tool.inputSchema && typeof tool.inputSchema === "object"
        ? tool.inputSchema
        : { type: "object", properties: {} };
      const desc = `[MCP:${conn.name}] ${tool.description || tool.name}`;
      openai.push({
        type: "function",
        function: { name: namespaced, description: desc, parameters: schema }
      });
      claude.push({ name: namespaced, description: desc, input_schema: schema });
    }
    return { openai, claude, count: openai.length };
  }

  async call(namespacedName, args = {}) {
    const entry = this.toolIndex.get(namespacedName);
    if (!entry) return { error: true, message: `MCP 工具不存在或服务器未连接: ${namespacedName}` };
    const conn = this.connections.get(entry.itemId);
    if (!conn?.client) return { error: true, message: `MCP 服务器「${conn?.name || entry.itemId}」未连接` };
    try {
      const result = await withTimeout(
        conn.client.callTool({ name: entry.originalName, arguments: args || {} }),
        60000,
        `MCP 工具 ${entry.originalName}`
      );
      const parts = [];
      if (Array.isArray(result?.content)) {
        for (const c of result.content) {
          if (c.type === "text") parts.push(c.text);
          else parts.push(`[${c.type || "content"}]`);
        }
      }
      if (result?.structuredContent) parts.push(JSON.stringify(result.structuredContent));
      return {
        success: !result?.isError,
        content: parts.join("\n").slice(0, 8000) || "(MCP 返回空内容)",
        isError: Boolean(result?.isError),
        server: conn.name,
        tool: entry.originalName
      };
    } catch (e) {
      return { error: true, message: `MCP 工具调用失败: ${e?.message || e}` };
    }
  }

  statusOf(itemId) {
    const conn = this.connections.get(itemId);
    if (!conn) return { itemId, connected: false, toolCount: 0, tools: [], error: null };
    return {
      itemId,
      name: conn.name,
      connected: Boolean(conn.client),
      toolCount: conn.tools.length,
      tools: conn.tools.map((t) => t.name),
      error: conn.error || null
    };
  }

  statusAll() {
    return this.listConfigs().map((item) => ({
      ...this.statusOf(item.id),
      name: item.name,
      enabled: Boolean(item.enabled)
    }));
  }

  async connectEnabled() {
    const items = this.listConfigs().filter((i) => i.enabled);
    const results = [];
    for (const item of items) {
      results.push(await this.connect(item));
    }
    return results;
  }
}

export default { McpManager };
