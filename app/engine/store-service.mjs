// Agent 插件 / 技能 / MCP 商店
// 存储：store_items 表；技能与插件的 config.prompt 会真实注入系统提示词
// MCP 条目为服务器配置登记（运行时由外部 MCP 客户端接入）

const KINDS = ['plugin', 'skill', 'mcp'];

const BUILTINS = [
  {
    kind: 'skill',
    name: '严谨工程',
    description: '强制用工具证据说话，禁止编造文件内容或命令输出',
    config: { prompt: '任何结论必须有工具执行证据；禁止编造文件内容、路径或命令输出。没有真实产出时，明确说明"未执行"。' }
  },
  {
    kind: 'skill',
    name: '代码审查',
    description: '按四维输出可执行的审查意见',
    config: { prompt: '审查代码时按 正确性 / 边界条件 / 性能 / 可维护性 四个维度给出问题，并标注文件路径与行号；只报可证实的问题。' }
  },
  {
    kind: 'plugin',
    name: '文件整理器',
    description: '整理文件前先盘点，操作后必须复查',
    config: { prompt: '执行文件整理任务时：1) 先用 list_directory 盘点现状；2) 再执行移动/重命名/写入；3) 最后必须用 list_directory 复查并报告真实结果。' }
  },
  {
    kind: 'mcp',
    name: '本地文件系统 MCP',
    description: 'stdio 方式挂载本地目录（配置登记）',
    config: {
      command: 'npx',
      args: ['-y', '@modelcontextprotocol/server-filesystem', 'E:\\ai群'],
      transport: 'stdio'
    }
  }
];

function nowIso() {
  return new Date().toISOString();
}

function parseConfig(v) {
  if (v && typeof v === 'object') return v;
  try {
    return v ? JSON.parse(v) : {};
  } catch {
    return {};
  }
}

export function ensureStoreSeed(db) {
  try {
    const count = db.prepare('SELECT COUNT(*) AS c FROM store_items').get()?.c || 0;
    if (count > 0) return;
    const insert = db.prepare(`
      INSERT INTO store_items (id, kind, name, description, config, enabled, source, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, 0, 'builtin', ?, ?)
    `);
    for (const item of BUILTINS) {
      const t = nowIso();
      insert.run(`st-${item.kind}-${item.name}`, item.kind, item.name, item.description, JSON.stringify(item.config || {}), t, t);
    }
    console.log('[Store] 已初始化内置条目');
  } catch (e) {
    console.warn('[Store] 内置条目初始化失败(忽略):', e?.message || e);
  }
}

// 已启用的技能/插件提示词（注入系统提示词，真实生效）
export function getEnabledStorePrompt(db) {
  try {
    const rows = db.prepare(
      "SELECT kind, name, config FROM store_items WHERE enabled = 1 AND kind IN ('skill','plugin') ORDER BY updated_at DESC"
    ).all();
    const lines = [];
    for (const r of rows) {
      const cfg = parseConfig(r.config);
      const prompt = String(cfg.prompt || '').trim();
      if (!prompt) continue;
      lines.push(`- ${r.kind === 'skill' ? '技能' : '插件'}「${r.name}」：${prompt}`);
    }
    if (lines.length === 0) return '';
    return `【已启用的技能与插件 · 必须遵守】\n${lines.join('\n')}`;
  } catch {
    return '';
  }
}

export function registerStoreEndpoints(app, db) {
  ensureStoreSeed(db);

  const listAll = db.prepare('SELECT * FROM store_items ORDER BY kind, created_at DESC');

  app.get('/api/store/items', (req, res) => {
    try {
      const kind = req.query.kind ? String(req.query.kind) : null;
      const rows = kind
        ? db.prepare('SELECT * FROM store_items WHERE kind = ? ORDER BY created_at DESC').all(kind)
        : listAll.all();
      res.json({ success: true, items: rows.map((r) => ({ ...r, config: parseConfig(r.config) })) });
    } catch (e) {
      res.status(500).json({ error: e?.message || '读取失败' });
    }
  });

  app.post('/api/store/items', (req, res) => {
    try {
      const body = req.body || {};
      const kind = String(body.kind || '').trim();
      const name = String(body.name || '').trim();
      if (!KINDS.includes(kind)) return res.status(400).json({ error: 'kind 必须是 plugin / skill / mcp' });
      if (!name) return res.status(400).json({ error: '名称不能为空' });
      const t = nowIso();
      const id = body.id ? String(body.id) : `st-${kind}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
      const exists = db.prepare('SELECT id FROM store_items WHERE id = ?').get(id);
      if (exists) {
        db.prepare(`
          UPDATE store_items SET kind = ?, name = ?, description = ?, config = ?, enabled = ?, source = ?, updated_at = ?
          WHERE id = ?
        `).run(kind, name, String(body.description || ''), JSON.stringify(body.config || {}),
          body.enabled ? 1 : 0, String(body.source || 'custom'), t, id);
      } else {
        db.prepare(`
          INSERT INTO store_items (id, kind, name, description, config, enabled, source, created_at, updated_at)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        `).run(id, kind, name, String(body.description || ''), JSON.stringify(body.config || {}),
          body.enabled === undefined ? 1 : (body.enabled ? 1 : 0), String(body.source || 'custom'), t, t);
      }
      const row = db.prepare('SELECT * FROM store_items WHERE id = ?').get(id);
      res.json({ success: true, item: { ...row, config: parseConfig(row.config) } });
    } catch (e) {
      res.status(500).json({ error: e?.message || '保存失败' });
    }
  });

  app.post('/api/store/items/:id/toggle', (req, res) => {
    try {
      const enabled = req.body?.enabled ? 1 : 0;
      const r = db.prepare('UPDATE store_items SET enabled = ?, updated_at = ? WHERE id = ?')
        .run(enabled, nowIso(), req.params.id);
      res.json({ success: r.changes > 0, enabled: Boolean(enabled) });
    } catch (e) {
      res.status(500).json({ error: e?.message || '更新失败' });
    }
  });

  app.delete('/api/store/items/:id', (req, res) => {
    try {
      const r = db.prepare('DELETE FROM store_items WHERE id = ?').run(req.params.id);
      res.json({ success: r.changes > 0 });
    } catch (e) {
      res.status(500).json({ error: e?.message || '删除失败' });
    }
  });

  // 导出（JSON）
  app.get('/api/store/export', (req, res) => {
    try {
      const kind = req.query.kind ? String(req.query.kind) : null;
      const rows = kind
        ? db.prepare('SELECT * FROM store_items WHERE kind = ? ORDER BY created_at DESC').all(kind)
        : listAll.all();
      const payload = {
        version: 1,
        exportedAt: nowIso(),
        items: rows.map((r) => ({
          kind: r.kind,
          name: r.name,
          description: r.description,
          config: parseConfig(r.config),
          enabled: Boolean(r.enabled),
          source: r.source
        }))
      };
      if (req.query.download === '1') {
        res.setHeader('Content-Disposition', 'attachment; filename="ai-temple-store.json"');
        res.setHeader('Content-Type', 'application/json; charset=utf-8');
      }
      res.json(payload);
    } catch (e) {
      res.status(500).json({ error: e?.message || '导出失败' });
    }
  });

  // 导入（接受导出结构 / items 数组 / 裸数组）
  app.post('/api/store/import', (req, res) => {
    try {
      const body = req.body || {};
      const raw = Array.isArray(body) ? body : (Array.isArray(body.items) ? body.items : null);
      if (!raw) return res.status(400).json({ error: '导入内容需包含 items 数组' });
      let added = 0;
      let updated = 0;
      let skipped = 0;
      const t = nowIso();
      for (const item of raw) {
        const kind = String(item?.kind || '').trim();
        const name = String(item?.name || '').trim();
        if (!KINDS.includes(kind) || !name) { skipped++; continue; }
        const hit = db.prepare('SELECT id FROM store_items WHERE kind = ? AND name = ?').get(kind, name);
        if (hit) {
          db.prepare('UPDATE store_items SET description = ?, config = ?, enabled = ?, source = ?, updated_at = ? WHERE id = ?')
            .run(String(item.description || ''), JSON.stringify(item.config || {}),
              item.enabled ? 1 : 0, 'imported', t, hit.id);
          updated++;
        } else {
          db.prepare(`
            INSERT INTO store_items (id, kind, name, description, config, enabled, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'imported', ?, ?)
          `).run(`st-${kind}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
            kind, name, String(item.description || ''), JSON.stringify(item.config || {}),
            item.enabled ? 1 : 0, t, t);
          added++;
        }
      }
      res.json({ success: true, added, updated, skipped });
    } catch (e) {
      res.status(500).json({ error: e?.message || '导入失败' });
    }
  });
}
