// 工具/设置/开放接口/编码搭档/笔记/工作日志 路由处理函数

import crypto from "node:crypto";
import path from "node:path";
import { spawn } from "node:child_process";


import {
  BACKUP_ROOT,
  INTELLIGENCE_LEVELS,
  REF_TEXT_EXTS,
  appIntelligenceLevel,
  appendJournal,
  autoBackupEnabled,
  broadcastCoding,
  buildCodingCommand,
  codingRuns,
  createCodingNote,
  createMessage,
  createNoteRecord,
  createNotification,
  db,
  detectCodingAgents,
  ensureOpenApiKey,
  envFlag,
  executeTool,
  findCodingAgent,
  getActiveProviderConfig,
  getBindHost,
  getCodingProviderEnv,
  getDefaultModel,
  getFamilyPassword,
  getMessagesBySession,
  getModelRoute,
  getModelRoutes,
  getNoteRecord,
  getOpenApiConfig,
  getOpenApiKeys,
  getScreenshotDir,
  getSession,
  getUsagePricing,
  getWorkspaceRoot,
  isLoopbackHost,
  listAgentsFn,
  mapAutonomousTask,
  notesToolApi,
  parseMaybeJson,
  quoteShellArg,
  recordTokenUsage,
  resolveCodingCwd,
  runAgentTurnLoop,
  saveModelRoutes,
  searchNoteRecords,
  sendWebhook,
  setAppSetting,
  setOpenApiKeys,
  taskQueue,
  toNote,
  writeEnvVars,
} from "../../server.mjs";

function nowIso() { return new Date().toISOString(); }

export const toolsHandlers = {
  // 翻译工具
  async translateText(req, res) {
    try {
      const { text, targetLang = "简体中文", sourceLang = "自动检测", model = null } = req.body || {};
      const input = String(text || "").trim();
      if (!input) return res.status(400).json({ error: "翻译内容不能为空" });
      if (input.length > 20000) return res.status(400).json({ error: "单次翻译最多 20000 字符" });
      const cfg = getActiveProviderConfig();
      if (!cfg.baseUrl || !cfg.apiKey) {
        return res.status(400).json({ error: `提供商「${cfg.key}」未配置 Base API Key` });
      }
      const translateModel = model || getModelRoute("translation") || cfg.model;
      const result = await runAgentTurnLoop({
        provider: cfg.protocol === "claude" ? "claude" : "openai",
        providerConfig: { baseUrl: cfg.baseUrl, apiKey: cfg.apiKey },
        model: translateModel,
        systemPrompt:
          `你是专业翻译。请把用户提供的文本翻译为「${targetLang}」。\n` +
          `要求：忠实原意、术语准确、保留段落与格式、只输出译文，不要解释、不要加引号。`,
        userPrompt: `源语言：${sourceLang}\n目标语言：${targetLang}\n\n${input}`,
        history: [],
        images: [],
        context: {},
        maxTurns: 1,
        temperature: 0.2,
        disableTools: true,
        onText: () => {},
        onToolStart: () => {},
        onToolResult: () => {}
      });
      const translation = String(result.content || "").trim();
      recordTokenUsage({
        sessionId: null, agentId: null, mode: "translate",
        model: translateModel, usage: result.usage
      });
      res.json({ success: true, translation, targetLang, sourceLang, usage: result.usage || null });
    } catch (e) {
      res.status(500).json({ error: e?.message || "翻译失败" });
    }
  },

  async textAction(req, res) {
    try {
      const action = String(req.body?.action || "").trim();
      const input = String(req.body?.text || "").trim();
      if (!input) return res.status(400).json({ error: "选中内容不能为空" });
      if (input.length > 12000) return res.status(400).json({ error: "单次最多 12000 字符" });
      const presets = {
        explain: {
          title: "解释",
          system: "你是讲解助手。只解释用户给出的文本，输出简洁中文，不要解释任务本身，不要客套。",
          user: (text) => `请解释下面这段文本：\n"""\n${text}\n"""`
        },
        summarize: {
          title: "总结",
          system: "你是总结助手。只总结用户给出的文本，输出简洁中文，不要解释任务本身，不要客套。",
          user: (text) => `请总结下面这段文本：\n"""\n${text}\n"""`
        }
      };
      const preset = presets[action];
      if (!preset) return res.status(400).json({ error: "未知操作" });
      const cfg = getActiveProviderConfig();
      if (!cfg.baseUrl || !cfg.apiKey) {
        return res.status(400).json({ error: `提供商「${cfg.key}」未配置 Base API Key` });
      }
      const model = getModelRoute("quick") || cfg.model;
      const result = await runAgentTurnLoop({
        provider: cfg.protocol === "claude" ? "claude" : "openai",
        providerConfig: { baseUrl: cfg.baseUrl, apiKey: cfg.apiKey },
        model,
        systemPrompt: preset.system,
        userPrompt: preset.user(input),
        history: [],
        images: [],
        context: {},
        maxTurns: 1,
        temperature: 0.3,
        disableTools: true,
        onText: () => {},
        onToolStart: () => {},
        onToolResult: () => {}
      });
      const output = String(result.content || "").trim();
      recordTokenUsage({
        sessionId: null, agentId: null, mode: `text-${action}`,
        model, usage: result.usage
      });
      res.json({ success: true, action, title: preset.title, result: output, usage: result.usage || null });
    } catch (e) {
      res.status(500).json({ error: e?.message || "处理失败" });
    }
  },

  async takeScreenshot(req, res) {
    try {
      const result = await executeTool("gui_screenshot", {}, {
        cwd: getWorkspaceRoot(),
        workspaceRoot: path.resolve(__dirname, ".."),
        permissionMode: "bypassPermissions",
        confirmed: true
      });
      if (result?.error) return res.status(400).json({ error: result.message || "截屏失败" });
      const filename = path.basename(result.path);
      res.json({
        success: true,
        path: result.path,
        filename,
        width: result.width,
        height: result.height,
        url: `/api/screenshot/${encodeURIComponent(filename)}?t=${Date.now()}`
      });
    } catch (e) {
      res.status(500).json({ error: e?.message || "截屏失败" });
    }
  },

  // 截图文件读取（/api/screenshot/:filename）
  async serveScreenshot(req, res) {
    try {
      const raw = String(req.params.filename || "");
      // 只允许纯文件名，挡掉路径穿越
      const name = path.basename(raw);
      if (!name || name !== raw || name === "." || name === "..") {
        return res.status(400).json({ error: "非法文件名" });
      }
      if (!/\.(png|jpe?g|webp|gif)$/i.test(name)) {
        return res.status(400).json({ error: "不支持的文件类型" });
      }
      const dir = getScreenshotDir();
      const abs = path.resolve(dir, name);
      const rel = path.relative(dir, abs);
      if (!rel || rel.startsWith("..") || path.isAbsolute(rel)) {
        return res.status(403).json({ error: "非法路径" });
      }
      if (!fs.existsSync(abs)) return res.status(404).json({ error: "截图不存在" });
      res.sendFile(abs);
    } catch (e) {
      res.status(500).json({ error: e?.message || "读取截图失败" });
    }
  },

  async getRecentScreenshots(req, res) {
    try {
      const dir = getScreenshotDir();
      if (!fs.existsSync(dir)) return res.json({ success: true, files: [] });
      const files = fs.readdirSync(dir, { withFileTypes: true })
        .filter((d) => d.isFile() && /\.(png|jpe?g)$/i.test(d.name))
        .map((d) => {
          const abs = path.join(dir, d.name);
          const stat = fs.statSync(abs);
          return { name: d.name, size: stat.size, mtime: stat.mtime.toISOString(), url: `/api/screenshot/${encodeURIComponent(d.name)}?t=${stat.mtimeMs}` };
        })
        .sort((a, b) => b.mtime.localeCompare(a.mtime))
        .slice(0, 24);
      res.json({ success: true, files });
    } catch (e) {
      res.status(500).json({ error: e?.message || "读取截图失败" });
    }
  },

  async getWorkspaceFiles(req, res) {
    try {
      const root = getWorkspaceRoot();
      const q = String(req.query.q || "").toLowerCase();
      const results = [];
      const walk = (dir, rel, level) => {
        if (level > 2 || results.length >= 300) return;
        let entries = [];
        try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
        entries.sort((a, b) => a.name.localeCompare(b.name));
        for (const entry of entries) {
          if (results.length >= 300) break;
          if (entry.name.startsWith(".") || entry.name === "node_modules") continue;
          const abs = path.join(dir, entry.name);
          const relPath = rel ? `${rel}/${entry.name}` : entry.name;
          if (entry.isDirectory()) {
            walk(abs, relPath, level + 1);
            continue;
          }
          if (!REF_TEXT_EXTS.has(path.extname(entry.name).toLowerCase())) continue;
          if (q && !relPath.toLowerCase().includes(q)) continue;
          let size = 0;
          try { size = fs.statSync(abs).size; } catch { /* 忽略 */ }
          results.push({ path: relPath, size });
        }
      };
      walk(root, "", 0);
      res.json({ success: true, root, files: results });
    } catch (error) {
      res.status(500).json({ error: error?.message || "读取工作区失败" });
    }
  },

  async readWorkspaceFile(req, res) {
    try {
      const root = getWorkspaceRoot();
      const rel = String(req.query.path || "");
      if (!rel) return res.status(400).json({ error: "缺少文件路径" });
      const abs = path.resolve(root, rel);
      const relCheck = path.relative(root, abs);
      if (!relCheck || relCheck.startsWith("..") || path.isAbsolute(relCheck)) {
        return res.status(403).json({ error: "只能引用工作区内的文件" });
      }
      if (!REF_TEXT_EXTS.has(path.extname(abs).toLowerCase())) {
        return res.status(400).json({ error: "该文件类型不支持 @ 引用" });
      }
      const stat = fs.statSync(abs);
      if (stat.size > 512 * 1024) {
        return res.status(400).json({ error: "文件超过 512KB，暂不支持引用" });
      }
      const content = fs.readFileSync(abs, "utf8");
      res.json({ success: true, path: rel, size: stat.size, content: content.slice(0, 100 * 1024) });
    } catch (error) {
      res.status(404).json({ error: error?.message || "文件不存在" });
    }
  },

  // 编码搭档
  async getCodingAgents(req, res) {
    try {
      const agents = detectCodingAgents(req.query.refresh === "1");
      res.json({ success: true, agents });
    } catch (e) {
      res.status(500).json({ error: e?.message || "检测编码工具失败" });
    }
  },

  async runCoding(req, res) {
    try {
      const goal = String(req.body?.goal || "").trim();
      const agentKey = String(req.body?.agentKey || "").trim();
      const agent = findCodingAgent(agentKey);
      if (!goal) return res.status(400).json({ error: "任务目标不能为空" });
      if (!agent) return res.status(404).json({ error: "未知编码代理" });
      const cwd = resolveCodingCwd(req.body?.cwd);
      if (!cwd) return res.status(400).json({ error: "工作目录不存在" });
      const template = String(req.body?.commandTemplate || agent.template || "").trim();
      if (!template) return res.status(400).json({ error: "请填写命令模板" });
      const model = String(req.body?.model || getModelRoute("coding") || "").trim() || getActiveProviderConfig().model;
      const command = buildCodingCommand(template, goal, model);
      const id = `coding-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const startedAt = new Date().toISOString();
      const run = {
        id, agentKey: agent.key, agentName: agent.name, goal, cwd, command, model,
        status: "running", output: "", exitCode: null, startedAt, finishedAt: null,
        noteId: null, listeners: new Set(), child: null, stopping: false
      };
      db.prepare(`
        INSERT INTO coding_runs (id, agent_key, goal, cwd, command, model, status, output, exit_code, started_at, finished_at, note_id)
        VALUES (?, ?, ?, ?, ?, ?, 'running', '', NULL, ?, NULL, NULL)
      `).run(id, agent.key, goal, cwd, command, model, startedAt);
      codingRuns.set(id, run);
      const env = getCodingProviderEnv(model);
      env.CODING_GOAL = goal;
      let child;
      try {
        child = spawn(command, [], { cwd, shell: true, windowsHide: true, env });
      } catch (e) {
        run.status = "failed";
        run.output = `启动失败：${e?.message || e}`;
        run.finishedAt = new Date().toISOString();
        db.prepare("UPDATE coding_runs SET status=?, output=?, finished_at=? WHERE id=?").run(run.status, run.output, run.finishedAt, id);
        codingRuns.delete(id);
        return res.status(500).json({ error: run.output });
      }
      run.child = child;
      child.stdout?.on("data", (buf) => {
        const text = buf.toString();
        run.output = (run.output + text).slice(-200_000);
        broadcastCoding(run, { type: "output", stream: "stdout", text });
      });
      child.stderr?.on("data", (buf) => {
        const text = buf.toString();
        run.output = (run.output + text).slice(-200_000);
        broadcastCoding(run, { type: "output", stream: "stderr", text });
      });
      child.on("error", (e) => {
        run.output = (run.output + `\n[启动错误] ${e?.message || e}\n`).slice(-200_000);
        broadcastCoding(run, { type: "error", message: e?.message || String(e) });
      });
      child.on("close", (code) => {
        run.exitCode = code;
        run.finishedAt = new Date().toISOString();
        run.status = run.stopping ? "stopped" : (code === 0 ? "completed" : "failed");
        try {
          const note = createCodingNote(run);
          if (note) run.noteId = note.id;
          db.prepare("UPDATE coding_runs SET status=?, output=?, exit_code=?, finished_at=?, note_id=? WHERE id=?")
            .run(run.status, run.output, code, run.finishedAt, run.noteId, id);
          appendJournal({
            taskId: null, sessionId: null, eventType: "coding_run",
            summary: `编码任务${run.status === "completed" ? "完成" : run.status === "stopped" ? "已停止" : "失败"}：${run.goal.slice(0, 120)}`,
            details: { agent: run.agentName, cwd: run.cwd, command: run.command, exitCode: code, model: run.model },
            artifacts: { noteId: run.noteId }
          });
          createNotification({
            type: "coding",
            title: `编码任务${run.status === "completed" ? "完成" : run.status === "stopped" ? "已停止" : "失败"}：${run.goal.slice(0, 60)}`,
            body: `${run.agentName} · exit ${code}${run.noteId ? " · 已生成笔记" : ""}`,
            link: `coding:${run.id}`
          });
          sendWebhook("coding.finished", {
            runId: run.id,
            goal: run.goal,
            status: run.status,
            exitCode: code,
            noteId: run.noteId || null
          });
        } catch (e) {
          console.warn("[Coding] 收尾失败(忽略):", e?.message || e);
        }
        broadcastCoding(run, { type: "done", status: run.status, exitCode: code, noteId: run.noteId });
        for (const listener of run.listeners) {
          try { listener.end(); } catch { /* 忽略 */ }
        }
        run.listeners.clear();
        setTimeout(() => codingRuns.delete(id), 60_000).unref?.();
      });
      res.json({ success: true, runId: id, command, cwd, model, agent: agent.name });
    } catch (e) {
      res.status(500).json({ error: e?.message || "启动编码任务失败" });
    }
  },

  async getCodingEvents(req, res) {
    const id = String(req.params.id || "");
    const run = codingRuns.get(id);
    if (!run) {
      const row = db.prepare("SELECT * FROM coding_runs WHERE id = ?").get(id);
      if (!row) return res.status(404).json({ error: "编码任务不存在" });
      res.writeHead(200, {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
        "X-Accel-Buffering": "no"
      });
      res.write(`data: ${JSON.stringify({ type: "output", text: row.output || "" })}\n\n`);
      res.write(`data: ${JSON.stringify({ type: "done", status: row.status, exitCode: row.exit_code, noteId: row.note_id })}\n\n`);
      res.end();
      return;
    }
    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no"
    });
    res.write(`data: ${JSON.stringify({ type: "output", text: run.output })}\n\n`);
    res.write(`data: ${JSON.stringify({ type: "status", status: run.status })}\n\n`);
    run.listeners.add(res);
    const heartbeat = setInterval(() => {
      try { res.write(": ping\n\n"); } catch { /* 忽略 */ }
    }, 15_000);
    req.on("close", () => {
      clearInterval(heartbeat);
      run.listeners.delete(res);
    });
  },

  async stopCoding(req, res) {
    const run = codingRuns.get(String(req.params.id || ""));
    if (!run) return res.status(404).json({ error: "编码任务不存在或已结束" });
    run.stopping = true;
    try {
      if (process.platform === "win32" && run.child?.pid) {
        spawn("taskkill", ["/pid", String(run.child.pid), "/T", "/F"], { windowsHide: true });
      } else {
        run.child?.kill("SIGTERM");
      }
      broadcastCoding(run, { type: "status", status: "stopping" });
      res.json({ success: true });
    } catch (e) {
      res.status(500).json({ error: e?.message || "停止失败" });
    }
  },

  async getCodingRuns(req, res) {
    try {
      const rows = db.prepare("SELECT * FROM coding_runs ORDER BY started_at DESC LIMIT 30").all();
      const runs = rows.map((r) => {
        const active = codingRuns.get(r.id);
        return {
          ...r,
          status: active ? active.status : r.status,
          active: Boolean(active)
        };
      });
      res.json({ success: true, runs });
    } catch (e) {
      res.status(500).json({ error: e?.message || "读取编码任务失败" });
    }
  },

  // 笔记
  async getNotes(req, res) {
    try {
      const q = String(req.query.q || "").trim();
      const owner = String(req.query.owner || "").trim();
      const visibility = String(req.query.visibility || "").trim();
      const limit = Math.min(Math.max(Number(req.query.limit) || 200, 1), 500);
      const clauses = [];
      const params = [];
      if (q) {
        clauses.push("(title LIKE ? OR content_md LIKE ?)");
        params.push(`%${q}%`, `%${q}%`);
      }
      if (owner) { clauses.push("owner = ?"); params.push(owner); }
      if (visibility) { clauses.push("visibility = ?"); params.push(visibility); }
      const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
      const rows = db.prepare(`SELECT * FROM notes ${where} ORDER BY pinned DESC, updated_at DESC LIMIT ?`).all(...params, limit);
      res.json({ success: true, notes: rows.map(toNote), count: rows.length });
    } catch (e) {
      res.status(500).json({ error: e?.message || "读取笔记失败" });
    }
  },

  async exportNotes(req, res) {
    try {
      const rows = db.prepare("SELECT * FROM notes ORDER BY updated_at DESC").all().map(toNote);
      res.setHeader("Content-Disposition", 'attachment; filename="ai-temple-notes.json"');
      res.json({ version: 1, exportedAt: nowIso(), notes: rows });
    } catch (e) {
      res.status(500).json({ error: e?.message || "导出笔记失败" });
    }
  },

  async importNotes(req, res) {
    try {
      const items = Array.isArray(req.body) ? req.body : (Array.isArray(req.body?.notes) ? req.body.notes : []);
      if (items.length === 0) return res.status(400).json({ error: "没有可导入的笔记" });
      let added = 0, updated = 0, skipped = 0;
      const now = new Date().toISOString();
      const upsert = db.transaction((list) => {
        for (const item of list.slice(0, 500)) {
          const title = String(item?.title || "").trim();
          const content = String(item?.content_md ?? item?.content ?? "").trim();
          if (!title || !content) { skipped++; continue; }
          const tags = JSON.stringify(Array.isArray(item.tags) ? item.tags.slice(0, 30).map(String) : []);
          const visibility = ["private", "shared", "agent"].includes(item.visibility) ? item.visibility : "shared";
          const owner = item.owner === "agent" ? "agent" : "human";
          const existing = item.id ? db.prepare("SELECT id FROM notes WHERE id = ?").get(String(item.id)) : null;
          if (existing) {
            db.prepare(`UPDATE notes SET title=?, content_md=?, tags=?, owner=?, visibility=?, task_id=?, pinned=?, updated_at=? WHERE id=?`)
              .run(title.slice(0, 200), content, tags, owner, visibility, item.task_id || null, item.pinned ? 1 : 0, nowIso(), existing.id);
            updated++;
          } else {
            const id = item.id ? String(item.id) : `note-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
            db.prepare(`INSERT INTO notes (id,title,content_md,tags,owner,visibility,task_id,pinned,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)`)
              .run(id, title.slice(0, 200), content, tags, owner, visibility, item.task_id || null, item.pinned ? 1 : 0, item.created_at || nowIso(), nowIso());
            added++;
          }
        }
      });
      upsert(items);
      res.json({ success: true, added, updated, skipped });
    } catch (e) {
      res.status(500).json({ error: e?.message || "导入笔记失败" });
    }
  },

  async getNote(req, res) {
    try {
      const note = getNoteRecord(req.params.id);
      if (!note) return res.status(404).json({ error: "笔记不存在" });
      res.json({ success: true, note });
    } catch (e) {
      res.status(500).json({ error: e?.message || "读取笔记失败" });
    }
  },

  async createNote(req, res) {
    try {
      const title = String(req.body?.title || "").trim();
      const content = String(req.body?.content_md ?? req.body?.content ?? "");
      if (!title) return res.status(400).json({ error: "标题不能为空" });
      const note = createNoteRecord({
        title, content_md: content,
        tags: Array.isArray(req.body?.tags) ? req.body.tags : [],
        owner: req.body?.owner === "agent" ? "agent" : "human",
        visibility: req.body?.visibility || "shared",
        task_id: req.body?.task_id || null,
        pinned: Boolean(req.body?.pinned)
      });
      res.json({ success: true, note });
    } catch (e) {
      res.status(500).json({ error: e?.message || "新建笔记失败" });
    }
  },

  async updateNote(req, res) {
    try {
      const existing = getNoteRecord(req.params.id);
      if (!existing) return res.status(404).json({ error: "笔记不存在" });
      const title = req.body?.title !== undefined ? String(req.body.title || "").trim() : existing.title;
      if (!title) return res.status(400).json({ error: "标题不能为空" });
      const content = req.body?.content_md !== undefined
        ? String(req.body.content_md || "")
        : (req.body?.content !== undefined ? String(req.body.content || "") : existing.content_md);
      const tags = req.body?.tags !== undefined && Array.isArray(req.body.tags)
        ? JSON.stringify(req.body.tags.slice(0, 30).map(String))
        : JSON.stringify(existing.tags);
      const visibility = ["private", "shared", "agent"].includes(req.body?.visibility) ? req.body.visibility : existing.visibility;
      const pinned = req.body?.pinned !== undefined ? (req.body.pinned ? 1 : 0) : (existing.pinned ? 1 : 0);
      const taskId = req.body?.task_id !== undefined ? (req.body.task_id || null) : existing.task_id;
      db.prepare(`UPDATE notes SET title=?, content_md=?, tags=?, visibility=?, task_id=?, pinned=?, updated_at=? WHERE id=?`)
        .run(title.slice(0, 200), content, tags, visibility, taskId, pinned, new Date().toISOString(), existing.id);
      res.json({ success: true, note: getNoteRecord(existing.id) });
    } catch (e) {
      res.status(500).json({ error: e?.message || "保存笔记失败" });
    }
  },

  async deleteNote(req, res) {
    try {
      const result = db.prepare("DELETE FROM notes WHERE id = ?").run(String(req.params.id || ""));
      if (result.changes === 0) return res.status(404).json({ error: "笔记不存在" });
      res.json({ success: true });
    } catch (e) {
      res.status(500).json({ error: e?.message || "删除笔记失败" });
    }
  },

  async getJournal(req, res) {
    try {
      const taskId = String(req.query.task_id || "").trim();
      const sessionId = String(req.query.session_id || "").trim();
      const limit = Math.min(Math.max(Number(req.query.limit) || 100, 1), 500);
      const clauses = [];
      const params = [];
      if (taskId) { clauses.push("task_id = ?"); params.push(taskId); }
      if (sessionId) { clauses.push("session_id = ?"); params.push(sessionId); }
      const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
      const rows = db.prepare(`SELECT * FROM agent_journal ${where} ORDER BY created_at DESC LIMIT ?`).all(...params, limit);
      res.json({
        success: true,
        entries: rows.map((r) => ({
          ...r,
          details: parseMaybeJson(r.details),
          artifacts: parseMaybeJson(r.artifacts)
        })),
        count: rows.length
      });
    } catch (e) {
      res.status(500).json({ error: e?.message || "读取工作日志失败" });
    }
  },

  async writeJournal(req, res) {
    try {
      const id = appendJournal({
        taskId: req.body?.task_id || req.body?.taskId || null,
        sessionId: req.body?.session_id || req.body?.sessionId || null,
        eventType: req.body?.event_type || req.body?.eventType || "note",
        summary: req.body?.summary || "",
        details: req.body?.details || null,
        artifacts: req.body?.artifacts || null
      });
      if (!id) return res.status(500).json({ error: "写入工作日志失败" });
      res.json({ success: true, id });
    } catch (e) {
      res.status(500).json({ error: e?.message || "写入工作日志失败" });
    }
  }
};

export const settingsHandlers = {
  async getSettings(req, res) {
    try {
      const countByKind = (kind) => {
        try {
          return db.prepare("SELECT COUNT(*) AS c FROM store_items WHERE kind = ? AND enabled = 1").get(kind)?.c || 0;
        } catch { return 0; }
      };
      res.json({
        success: true,
        tools: {
          fileTools: envFlag("FILE_TOOLS_ENABLED"),
          terminal: envFlag("TERMINAL_ENABLED"),
          gui: envFlag("GUI_ENABLED"),
          screenCapture: envFlag("SCREEN_CAPTURE_ENABLED"),
          sandbox: envFlag("SANDBOX_MODE", "0")
        },
        preferences: {
          intelligenceLevel: appIntelligenceLevel,
          maxAgents: Number(process.env.MAX_AGENTS) || 3,
          defaultMode: process.env.DEFAULT_CHAT_MODE || "group",
          defaultCwd: process.env.DEFAULT_WORK_DIR || "workspace"
        },
        pricing: getUsagePricing(),
        modelRoutes: getModelRoutes(),
        backup: {
          auto: autoBackupEnabled(),
          dir: BACKUP_ROOT
        },
        counts: {
          skills: countByKind("skill"),
          plugins: countByKind("plugin"),
          mcp: countByKind("mcp")
        },
        family: {
          enabled: Boolean(getFamilyPassword()),
          bindHost: getBindHost(),
          lanAccess: !isLoopbackHost(getBindHost())
        },
        version: {
          app: "AI 神殿",
          version: "2.9",
          build: "2026-10-19",
          node: process.version
        }
      });
    } catch (e) {
      res.status(500).json({ error: e?.message || "读取设置失败" });
    }
  },

  async saveToolsSettings(req, res) {
    try {
      const body = req.body || {};
      const map = {
        fileTools: "FILE_TOOLS_ENABLED",
        terminal: "TERMINAL_ENABLED",
        gui: "GUI_ENABLED",
        screenCapture: "SCREEN_CAPTURE_ENABLED",
        sandbox: "SANDBOX_MODE"
      };
      const vars = {};
      for (const [key, envKey] of Object.entries(map)) {
        if (body[key] !== undefined) vars[envKey] = body[key] ? "1" : "0";
      }
      if (Object.keys(vars).length === 0) return res.status(400).json({ error: "没有可保存的开关" });
      writeEnvVars(vars);
      res.json({
        success: true,
        tools: {
          fileTools: envFlag("FILE_TOOLS_ENABLED"),
          terminal: envFlag("TERMINAL_ENABLED"),
          gui: envFlag("GUI_ENABLED"),
          screenCapture: envFlag("SCREEN_CAPTURE_ENABLED"),
          sandbox: envFlag("SANDBOX_MODE", "0")
        }
      });
    } catch (e) {
      res.status(500).json({ error: e?.message || "保存工具设置失败" });
    }
  },

  async savePreferences(req, res) {
    try {
      const { intelligenceLevel, maxAgents, defaultMode, defaultCwd } = req.body || {};
      const vars = {};
      if (intelligenceLevel !== undefined) {
        const level = Number(intelligenceLevel);
        if (!INTELLIGENCE_LEVELS.some((l) => l.level === level)) {
          return res.status(400).json({ error: "智力档位必须是 1-6" });
        }
        appIntelligenceLevel = level;
        vars.AI_INTELLIGENCE_LEVEL = String(level);
      }
      if (maxAgents !== undefined) {
        const n = Math.max(1, Math.min(8, Number(maxAgents) || 3));
        vars.MAX_AGENTS = String(n);
      }
      if (defaultMode !== undefined) {
        const allowed = ["group", "private", "workflow", "proactive", "continuous", "creative", "analysis", "autonomous", "cross_platform"];
        if (!allowed.includes(String(defaultMode))) return res.status(400).json({ error: "默认模式不合法" });
        vars.DEFAULT_CHAT_MODE = String(defaultMode);
      }
      if (defaultCwd !== undefined) {
        vars.DEFAULT_WORK_DIR = String(defaultCwd).slice(0, 260) || "workspace";
      }
      if (Object.keys(vars).length === 0) return res.status(400).json({ error: "没有可保存的偏好" });
      writeEnvVars(vars);
      res.json({
        success: true,
        preferences: {
          intelligenceLevel: appIntelligenceLevel,
          maxAgents: Number(process.env.MAX_AGENTS) || 3,
          defaultMode: process.env.DEFAULT_CHAT_MODE || "group",
          defaultCwd: process.env.DEFAULT_WORK_DIR || "workspace"
        }
      });
    } catch (e) {
      res.status(500).json({ error: e?.message || "保存偏好失败" });
    }
  },

  async savePricing(req, res) {
    try {
      const { input, output, cached, currency } = req.body || {};
      const clean = (v) => {
        const n = Number(v);
        return Number.isFinite(n) && n >= 0 ? n : 0;
      };
      const vars = {
        USAGE_INPUT_PRICE: String(clean(input)),
        USAGE_OUTPUT_PRICE: String(clean(output)),
        USAGE_CACHED_PRICE: String(clean(cached)),
        USAGE_CURRENCY: String(currency || "CNY").toUpperCase() === "USD" ? "USD" : "CNY"
      };
      writeEnvVars(vars);
      res.json({ success: true, pricing: getUsagePricing() });
    } catch (e) {
      res.status(500).json({ error: e?.message || "保存计费设置失败" });
    }
  },

  async saveModelRoutes(req, res) {
    try {
      const routes = saveModelRoutes(req.body || {});
      res.json({ success: true, modelRoutes: routes });
    } catch (e) {
      res.status(500).json({ error: e?.message || "保存模型分工失败" });
    }
  },

  async saveFamilySettings(req, res) {
    try {
      const enabled = Boolean(req.body?.enabled);
      const input = String(req.body?.password || "").trim();
      const current = getFamilyPassword();
      if (enabled && !input && !current) {
        return res.status(400).json({ error: "启用家庭访问前请设置口令" });
      }
      if (enabled && input && input.length < 4) {
        return res.status(400).json({ error: "家庭访问口令至少 4 位" });
      }
      const next = enabled ? (input || current) : "";
      writeEnvVars({ FAMILY_ACCESS_PASSWORD: next });
      if (!next) {
        res.setHeader("Set-Cookie", "family_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0");
      }
      res.json({
        success: true,
        family: {
          enabled: Boolean(next),
          bindHost: getBindHost(),
          lanAccess: !isLoopbackHost(getBindHost())
        },
        requireLogin: Boolean(next) && next !== current
      });
    } catch (e) {
      res.status(500).json({ error: e?.message || "保存家庭访问设置失败" });
    }
  },

  async saveBackupSettings(req, res) {
    try {
      setAppSetting("auto_backup", req.body?.auto === false ? "0" : "1");
      res.json({ success: true, auto: autoBackupEnabled() });
    } catch (e) {
      res.status(500).json({ error: e?.message || "保存备份设置失败" });
    }
  }
};

export const openHandlers = {
  async getOpenApiConfig(req, res) {
    try {
      const cfg = getOpenApiConfig();
      const keys = cfg.keys.map(k => ({
        ...k,
        key: k.key ? `${k.key.slice(0, 6)}…${k.key.slice(-6)}` : '',
        keyFull: k.key
      }));
      res.json({
        success: true,
        enabled: cfg.enabled,
        keys,
        webhookUrl: cfg.webhookUrl,
        webhookEnabled: cfg.webhookEnabled,
        endpoints: {
          chat: "/api/open/chat",
          tasks: "/api/open/tasks",
          taskDetail: "/api/open/tasks/:id",
          health: "/api/open/health"
        }
      });
    } catch (e) {
      res.status(500).json({ error: e?.message || "读取开放接口配置失败" });
    }
  },

  async saveOpenApiConfig(req, res) {
    try {
      const body = req.body || {};
      if (body.enabled !== void 0) setAppSetting("open_api_enabled", body.enabled ? "1" : "0");
      if (body.webhookEnabled !== void 0) setAppSetting("webhook_enabled", body.webhookEnabled ? "1" : "0");
      if (body.webhookUrl !== void 0) setAppSetting("webhook_url", String(body.webhookUrl || "").slice(0, 1000));
      
      const keys = getOpenApiKeys();
      if (body.keys) {
        setOpenApiKeys(body.keys);
      } else if (body.addKey) {
        const newKey = {
          id: `key-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          name: body.addKey.name || `Key ${keys.length + 1}`,
          key: crypto.randomBytes(24).toString("hex"),
          permissions: body.addKey.permissions || ['read'],
          createdAt: new Date().toISOString(),
          lastUsed: null
        };
        keys.push(newKey);
        setOpenApiKeys(keys);
      } else if (body.removeKey) {
        const idx = keys.findIndex(k => k.id === body.removeKey);
        if (idx >= 0) keys.splice(idx, 1);
        setOpenApiKeys(keys);
      } else if (body.updateKey) {
        const idx = keys.findIndex(k => k.id === body.updateKey.id);
        if (idx >= 0) {
          keys[idx] = { ...keys[idx], ...body.updateKey };
          setOpenApiKeys(keys);
        }
      } else if (body.regenerateKey || keys.length === 0) {
        ensureOpenApiKey();
      } else if (body.enabled) {
        ensureOpenApiKey();
      }
      
      const cfg = getOpenApiConfig();
      res.json({ success: true, enabled: cfg.enabled, keys: cfg.keys, webhookUrl: cfg.webhookUrl, webhookEnabled: cfg.webhookEnabled });
    } catch (e) {
      res.status(500).json({ error: e?.message || "保存开放接口配置失败" });
    }
  },

  async testWebhook(req, res) {
    try {
      const ok = await sendWebhook("webhook.test", { message: "AI 神殿 Webhook 测试" });
      res.json({ success: ok, message: ok ? "已发送测试请求" : "未启用或推送失败" });
    } catch (e) {
      res.status(500).json({ error: e?.message || "测试失败" });
    }
  },

  async openHealth(req, res) {
    res.json({ success: true, app: "AI 神殿", version: "2.9", time: nowIso() });
  },

  async openChat(req, res) {
    try {
      const message = String(req.body?.message || "").trim();
      if (!message) return res.status(400).json({ error: "message 不能为空" });
      const sessionId = req.body?.sessionId ? String(req.body.sessionId) : null;
      const session = sessionId ? getSession(sessionId) : null;
      if (sessionId && !session) return res.status(404).json({ error: "会话不存在" });
      const cfg = getActiveProviderConfig();
      if (!cfg.baseUrl || !cfg.apiKey) {
        return res.status(400).json({ error: `提供商「${cfg.key}」未配置 Base URL 或 API Key` });
      }
      const model = String(req.body?.model || getDefaultModel() || cfg.model);
      const history = session
        ? getMessagesBySession(session.id).slice(-10).map((m) => ({ role: m.role, content: m.content }))
        : [];
      const now = nowIso();
      if (session) {
        createMessage({
          id: uuidv4(), session_id: session.id, role: "user", content: message,
          model: null, created_at: now
        });
      }
      const result = await runAgentTurnLoop({
        provider: cfg.protocol === "claude" ? "claude" : "openai",
        providerConfig: { baseUrl: cfg.baseUrl, apiKey: cfg.apiKey },
        model,
        systemPrompt: "你是 AI 神殿开放接口助手。请用简洁中文回答外部系统的问题，只输出回答正文。",
        userPrompt: message,
        history,
        images: [],
        context: {},
        maxTurns: 1,
        temperature: 0.4,
        disableTools: true,
        onText: () => {},
        onToolStart: () => {},
        onToolResult: () => {}
      });
      const reply = String(result.content || "").trim();
      if (session) {
        createMessage({
          id: uuidv4(), session_id: session.id, role: "assistant", content: reply,
          model, created_at: new Date().toISOString(),
          agent_name: "开放接口", agent_color: "#22d3ee", reply_source: "open_api"
        });
        db.prepare("UPDATE sessions SET updated_at = ? WHERE id = ?").run(new Date().toISOString(), session.id);
      }
      recordTokenUsage({
        sessionId: session?.id || null, agentId: null, mode: "open_api",
        model, usage: result.usage
      });
      res.json({ success: true, reply, sessionId: session?.id || null, model, usage: result.usage || null });
    } catch (e) {
      res.status(500).json({ error: e?.message || "开放接口调用失败" });
    }
  },

  async createOpenTask(req, res) {
    try {
      const body = req.body || {};
      const goal = String(body.goal || "").trim();
      const sessionId = String(body.sessionId || "").trim();
      if (!goal) return res.status(400).json({ error: "goal 不能为空" });
      if (!sessionId) return res.status(400).json({ error: "sessionId 不能为空" });
      if (!getSession(sessionId)) return res.status(404).json({ error: "会话不存在" });
      const task = taskQueue.enqueue({
        sessionId, goal,
        priority: Number(body.priority) || 0,
        dueAt: body.dueAt || null,
        allowWrites: body.allowWrites ? 1 : 0,
        origin: "open_api"
      });
      res.json({ success: true, task: mapAutonomousTask(task) });
    } catch (e) {
      res.status(500).json({ error: e?.message || "创建任务失败" });
    }
  },

  async getOpenTask(req, res) {
    try {
      const row = db.prepare("SELECT * FROM autonomous_tasks WHERE id = ?").get(req.params.id);
      if (!row) return res.status(404).json({ error: "任务不存在" });
      res.json({ success: true, task: mapAutonomousTask(row) });
    } catch (e) {
      res.status(500).json({ error: e?.message || "读取任务失败" });
    }
  }
};