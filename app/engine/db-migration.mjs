
import fs from 'node:fs';
import path from 'node:path';

// 幂等补列：表已存在但缺字段时补齐（老库升级用）
function ensureColumnSafe(db, table, column, definition) {
  try {
    const columns = db.prepare(`PRAGMA table_info(${table})`).all().map((c) => c.name);
    if (!columns.includes(column)) {
      db.exec(`ALTER TABLE ${table} ADD COLUMN ${column} ${definition}`);
      console.log(`[Migration] Added column '${column}' to ${table}.`);
    }
  } catch (e) {
    console.warn(`[Migration] 补列 ${table}.${column} 失败(忽略):`, e?.message || e);
  }
}

export function runDatabaseMigrations(db) {
  try {
    // 1. 扩展 sessions 表
    const tableInfo = db.prepare("PRAGMA table_info(sessions)").all();
    const columns = tableInfo.map(col => col.name);
    
    if (!columns.includes('mode')) {
      db.prepare("ALTER TABLE sessions ADD COLUMN mode TEXT DEFAULT 'group'").run();
      console.log("[Migration] Added column 'mode' to sessions table.");
    }
    if (!columns.includes('target_agent_id')) {
      db.prepare("ALTER TABLE sessions ADD COLUMN target_agent_id TEXT").run();
      console.log("[Migration] Added column 'target_agent_id' to sessions table.");
    }
    if (!columns.includes('proactive_enabled')) {
      db.prepare("ALTER TABLE sessions ADD COLUMN proactive_enabled INTEGER DEFAULT 1").run();
      console.log("[Migration] Added column 'proactive_enabled' to sessions table.");
    }
    if (!columns.includes('context_memory_enabled')) {
      db.prepare("ALTER TABLE sessions ADD COLUMN context_memory_enabled INTEGER DEFAULT 1").run();
      console.log("[Migration] Added column 'context_memory_enabled' to sessions table.");
    }

    // 1.1 群资料与群成员（v2.2）
    ensureColumnSafe(db, 'sessions', 'avatar', "TEXT DEFAULT '👥'");
    ensureColumnSafe(db, 'sessions', 'category', "TEXT DEFAULT '家庭'");
    ensureColumnSafe(db, 'sessions', 'tags', "TEXT DEFAULT '[]'");
    ensureColumnSafe(db, 'sessions', 'intro', "TEXT DEFAULT ''");
    ensureColumnSafe(db, 'sessions', 'location', "TEXT DEFAULT ''");
    ensureColumnSafe(db, 'sessions', 'announcement', "TEXT DEFAULT ''");
    ensureColumnSafe(db, 'sessions', 'my_nickname', "TEXT DEFAULT ''");
    ensureColumnSafe(db, 'sessions', 'remark', "TEXT DEFAULT ''");
    ensureColumnSafe(db, 'sessions', 'pinned', 'INTEGER DEFAULT 0');
    ensureColumnSafe(db, 'sessions', 'muted', 'INTEGER DEFAULT 0');
    ensureColumnSafe(db, 'sessions', 'folded', 'INTEGER DEFAULT 0');
    ensureColumnSafe(db, 'sessions', 'saved', 'INTEGER DEFAULT 1');
    ensureColumnSafe(db, 'sessions', 'group_no', 'TEXT');
    ensureColumnSafe(db, 'sessions', 'member_limit', 'INTEGER DEFAULT 20');
    ensureColumnSafe(db, 'sessions', 'join_policy', "TEXT DEFAULT 'manual'");
    ensureColumnSafe(db, 'sessions', 'last_read_at', 'TEXT');
    // v2.6：成员发言频率（quiet / standard / active / mention）
    ensureColumnSafe(db, 'sessions', 'speech_mode', "TEXT DEFAULT 'standard'");
    // v2.6：引用回复
    ensureColumnSafe(db, 'messages', 'reply_to_id', 'TEXT');
    // v2.9：消息撤回
    ensureColumnSafe(db, 'messages', 'recalled', 'INTEGER DEFAULT 0');
    ensureColumnSafe(db, 'messages', 'recalled_at', 'TEXT');
    // v2.9：群成员发言顺序
    ensureColumnSafe(db, 'session_agents', 'sort_order', 'INTEGER DEFAULT 0');

    // 老群把最后已读时间初始化为更新时间，避免升级后出现满屏未读
    try {
      const r = db.prepare("UPDATE sessions SET last_read_at = updated_at WHERE last_read_at IS NULL OR last_read_at = ''").run();
      if (r.changes > 0) console.log(`[Migration] 已初始化 ${r.changes} 个群的最后已读时间。`);
    } catch (e) {
      console.warn('[Migration] 初始化最后已读时间失败(忽略):', e?.message || e);
    }

    db.exec(`
      CREATE TABLE IF NOT EXISTS session_agents (
        session_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        enabled INTEGER DEFAULT 1,
        permissions TEXT DEFAULT '{}',
        joined_at TEXT NOT NULL,
        PRIMARY KEY (session_id, agent_id)
      );
      CREATE INDEX IF NOT EXISTS idx_session_agents_session ON session_agents(session_id);
      CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_group_no ON sessions(group_no);
    `);

    // 群号回填：8 位数字，确保唯一
    try {
      const needNo = db.prepare("SELECT id FROM sessions WHERE group_no IS NULL OR group_no = ''").all();
      if (needNo.length > 0) {
        const exists = db.prepare("SELECT 1 FROM sessions WHERE group_no = ?");
        const setNo = db.prepare("UPDATE sessions SET group_no = ? WHERE id = ?");
        const backfill = db.transaction((rows) => {
          for (const row of rows) {
            let no = '';
            do { no = String(Math.floor(10000000 + Math.random() * 90000000)); } while (exists.get(no));
            setNo.run(no, row.id);
          }
        });
        backfill(needNo);
        console.log(`[Migration] 已为 ${needNo.length} 个群生成群号。`);
      }
    } catch (e) {
      console.warn('[Migration] 群号回填失败(忽略):', e?.message || e);
    }

    // 群成员初始化：老群默认全员，保持原有行为
    try {
      const memberCount = db.prepare("SELECT COUNT(*) AS c FROM session_agents").get().c;
      if (memberCount === 0) {
        const r = db.prepare(`
          INSERT OR IGNORE INTO session_agents (session_id, agent_id, enabled, permissions, joined_at)
          SELECT s.id, a.id, 1, '{}', datetime('now')
          FROM sessions s CROSS JOIN agents a WHERE a.enabled = 1
        `).run();
        if (r.changes > 0) console.log(`[Migration] 已初始化 ${r.changes} 条群成员记录。`);
      }
    } catch (e) {
      console.warn('[Migration] 群成员初始化失败(忽略):', e?.message || e);
    }

    // 2. 创建主动服务表
    db.exec(`
      CREATE TABLE IF NOT EXISTS proactive_services (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        service_type TEXT NOT NULL,
        trigger_condition TEXT,
        action TEXT,
        status TEXT DEFAULT 'pending',
        priority INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        executed_at TEXT,
        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
      );
      CREATE INDEX IF NOT EXISTS idx_proactive_session ON proactive_services(session_id);
      CREATE INDEX IF NOT EXISTS idx_proactive_status ON proactive_services(status);
    `);
    console.log("[Migration] Created proactive_services table.");

    // 3. 创建任务跟踪表
    db.exec(`
      CREATE TABLE IF NOT EXISTS task_tracking (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        task_name TEXT NOT NULL,
        task_description TEXT,
        status TEXT DEFAULT 'pending',
        progress INTEGER DEFAULT 0,
        priority INTEGER DEFAULT 0,
        assigned_to TEXT,
        due_date TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT,
        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
      );
      CREATE INDEX IF NOT EXISTS idx_task_session ON task_tracking(session_id);
      CREATE INDEX IF NOT EXISTS idx_task_status ON task_tracking(status);
    `);
    console.log("[Migration] Created task_tracking table.");

    // 4. 创建多层次记忆库表
    db.exec(`
      CREATE TABLE IF NOT EXISTS context_memory (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        memory_type TEXT NOT NULL,
        content TEXT NOT NULL,
        importance INTEGER DEFAULT 0,
        access_count INTEGER DEFAULT 0,
        last_accessed TEXT,
        expires_at TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
      );
      CREATE INDEX IF NOT EXISTS idx_memory_session ON context_memory(session_id);
      CREATE INDEX IF NOT EXISTS idx_memory_type ON context_memory(memory_type);
      CREATE INDEX IF NOT EXISTS idx_memory_expires ON context_memory(expires_at);
    `);
    console.log("[Migration] Created context_memory table.");

    // 5. 创建知识图谱表
    db.exec(`
      CREATE TABLE IF NOT EXISTS knowledge_graph (
        id TEXT PRIMARY KEY,
        entity_name TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        properties TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS knowledge_edges (
        id TEXT PRIMARY KEY,
        source_entity_id TEXT NOT NULL,
        target_entity_id TEXT NOT NULL,
        relationship_type TEXT NOT NULL,
        properties TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (source_entity_id) REFERENCES knowledge_graph(id),
        FOREIGN KEY (target_entity_id) REFERENCES knowledge_graph(id)
      );
      CREATE INDEX IF NOT EXISTS idx_kg_entity ON knowledge_graph(entity_name);
      CREATE INDEX IF NOT EXISTS idx_ke_source ON knowledge_edges(source_entity_id);
      CREATE INDEX IF NOT EXISTS idx_ke_target ON knowledge_edges(target_entity_id);
    `);
    console.log("[Migration] Created knowledge_graph and knowledge_edges tables.");

    // 6. 创建自主学习记录表
    db.exec(`
      CREATE TABLE IF NOT EXISTS learning_records (
        id TEXT PRIMARY KEY,
        agent_id TEXT NOT NULL,
        interaction_type TEXT NOT NULL,
        input TEXT,
        output TEXT,
        feedback TEXT,
        score REAL,
        created_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_learning_agent ON learning_records(agent_id);
      CREATE INDEX IF NOT EXISTS idx_learning_type ON learning_records(interaction_type);
    `);
    console.log("[Migration] Created learning_records table.");

    // 7. 创建工作流模板表
    db.exec(`
      CREATE TABLE IF NOT EXISTS workflow_templates (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT,
        stages TEXT NOT NULL,
        category TEXT,
        enabled INTEGER DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );
    `);
    console.log("[Migration] Created workflow_templates table.");

    // 8. 创建自主代理配置表
    db.exec(`
      CREATE TABLE IF NOT EXISTS agent_configs (
        id TEXT PRIMARY KEY,
        agent_id TEXT NOT NULL UNIQUE,
        autonomy_level INTEGER DEFAULT 1,
        proactive_enabled INTEGER DEFAULT 0,
        learning_enabled INTEGER DEFAULT 0,
        memory_retention_days INTEGER DEFAULT 30,
        custom_instructions TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE
      );
    `);
    console.log("[Migration] Created agent_configs table.");

    // 9. 提示词配置表（系统/步骤/风格人设，可强制约束）
    db.exec(`
      CREATE TABLE IF NOT EXISTS prompt_config (
        id TEXT PRIMARY KEY,
        scope TEXT NOT NULL,
        system_prompt TEXT,
        step_prompt TEXT,
        style_prompt TEXT,
        enforce INTEGER DEFAULT 0,
        updated_at TEXT NOT NULL
      );
    `);
    console.log("[Migration] Created prompt_config table.");

    // 10. 执行审计日志表（看清 AI 到底真做了什么）
    db.exec(`
      CREATE TABLE IF NOT EXISTS audit_logs (
        id TEXT PRIMARY KEY,
        session_id TEXT,
        agent_id TEXT,
        agent_name TEXT,
        mode TEXT,
        tool_name TEXT NOT NULL,
        tool_input TEXT,
        tool_output TEXT,
        status TEXT NOT NULL,
        duration_ms INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_logs(session_id);
      CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at);
      CREATE INDEX IF NOT EXISTS idx_audit_status ON audit_logs(status);
    `);
    console.log("[Migration] Created audit_logs table.");

    // 11. Token 与上下文用量表（每次模型调用记录）
    db.exec(`
      CREATE TABLE IF NOT EXISTS token_usage (
        id TEXT PRIMARY KEY,
        session_id TEXT,
        agent_id TEXT,
        mode TEXT,
        model TEXT,
        prompt_tokens INTEGER DEFAULT 0,
        completion_tokens INTEGER DEFAULT 0,
        total_tokens INTEGER DEFAULT 0,
        context_messages INTEGER DEFAULT 0,
        context_chars INTEGER DEFAULT 0,
        estimated INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_token_session ON token_usage(session_id);
      CREATE INDEX IF NOT EXISTS idx_token_created ON token_usage(created_at);
    `);
    console.log("[Migration] Created token_usage table.");
    ensureColumnSafe(db, 'token_usage', 'cached_tokens', 'INTEGER DEFAULT 0');

    // 12. Agent 插件 / 技能 / MCP 商店
    db.exec(`
      CREATE TABLE IF NOT EXISTS store_items (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        description TEXT,
        config TEXT,
        enabled INTEGER DEFAULT 1,
        source TEXT DEFAULT 'custom',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_store_kind ON store_items(kind);
      CREATE INDEX IF NOT EXISTS idx_store_enabled ON store_items(enabled);
    `);
    console.log("[Migration] Created store_items table.");

    // 13. 自主任务检查点（支持断点续跑）
    db.exec(`
      CREATE TABLE IF NOT EXISTS autonomous_tasks (
        id TEXT PRIMARY KEY,
        session_id TEXT,
        goal TEXT NOT NULL,
        plan TEXT,
        step_results TEXT,
        total_steps INTEGER DEFAULT 0,
        current_step INTEGER DEFAULT 0,
        status TEXT DEFAULT 'running',
        last_error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT
      );
      CREATE INDEX IF NOT EXISTS idx_autotask_session ON autonomous_tasks(session_id);
      CREATE INDEX IF NOT EXISTS idx_autotask_status ON autonomous_tasks(status);
    `);
    console.log("[Migration] Created autonomous_tasks table.");

    // 14. 主动服务反馈与权重（L2 建议排序学习）
    ensureColumnSafe(db, 'proactive_services', 'feedback', 'TEXT');
    ensureColumnSafe(db, 'proactive_services', 'feedback_at', 'TEXT');
    ensureColumnSafe(db, 'proactive_services', 'weight', 'REAL DEFAULT 1');

    // 15. 自主任务队列字段（L3 自动调度 / 重启恢复 / 长程目标）
    ensureColumnSafe(db, 'autonomous_tasks', 'priority', 'INTEGER DEFAULT 0');
    ensureColumnSafe(db, 'autonomous_tasks', 'due_at', 'TEXT');
    ensureColumnSafe(db, 'autonomous_tasks', 'goal_id', 'TEXT');
    ensureColumnSafe(db, 'autonomous_tasks', 'model', 'TEXT');
    ensureColumnSafe(db, 'autonomous_tasks', 'allow_writes', 'INTEGER DEFAULT 1');
    ensureColumnSafe(db, 'autonomous_tasks', 'replan_count', 'INTEGER DEFAULT 0');
    ensureColumnSafe(db, 'autonomous_tasks', 'auto_retry', 'INTEGER DEFAULT 0');
    ensureColumnSafe(db, 'autonomous_tasks', 'origin', "TEXT DEFAULT 'manual'");
    ensureColumnSafe(db, 'autonomous_tasks', 'started_at', 'TEXT');
    ensureColumnSafe(db, 'autonomous_tasks', 'recurrence', "TEXT DEFAULT 'none'");
    ensureColumnSafe(db, 'autonomous_tasks', 'recur_time', "TEXT DEFAULT '09:00'");
    ensureColumnSafe(db, 'autonomous_tasks', 'recur_weekday', 'INTEGER DEFAULT 1');
    ensureColumnSafe(db, 'autonomous_tasks', 'recur_monthday', 'INTEGER DEFAULT 1');
    ensureColumnSafe(db, 'autonomous_tasks', 'max_retry', 'INTEGER DEFAULT 2');
    ensureColumnSafe(db, 'autonomous_tasks', 'timeout_sec', 'INTEGER DEFAULT 900');
    // v2.9 策略效果回归字段
    ensureColumnSafe(db, 'autonomous_tasks', 'matched_strategy_ids', 'TEXT');
    ensureColumnSafe(db, 'autonomous_tasks', 'task_start_time', 'INTEGER');

    // 16. 跨会话用户画像（L2）
    db.exec(`
      CREATE TABLE IF NOT EXISTS user_profiles (
        id TEXT PRIMARY KEY,
        summary TEXT,
        topics TEXT,
        intents TEXT,
        session_count INTEGER DEFAULT 0,
        message_count INTEGER DEFAULT 0,
        updated_at TEXT NOT NULL
      );
    `);
    console.log("[Migration] Created user_profiles table.");

    // 17. 策略库：高分执行路径沉淀为可复用技能（L3）
    db.exec(`
      CREATE TABLE IF NOT EXISTS strategy_library (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        goal_pattern TEXT,
        keywords TEXT,
        steps TEXT NOT NULL,
        avg_score REAL DEFAULT 0,
        use_count INTEGER DEFAULT 0,
        source_task TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        -- v2.9 策略效果回归字段
        reuse_count INTEGER DEFAULT 0,
        reuse_success_count INTEGER DEFAULT 0,
        reuse_avg_score REAL DEFAULT 0,
        reuse_total_duration_ms INTEGER DEFAULT 0,
        last_reused_at TEXT
      );
      CREATE INDEX IF NOT EXISTS idx_strategy_score ON strategy_library(avg_score);
    `);
    console.log("[Migration] Created strategy_library table.");

    // v2.9 策略库补列
    ensureColumnSafe(db, 'strategy_library', 'reuse_count', 'INTEGER DEFAULT 0');
    ensureColumnSafe(db, 'strategy_library', 'reuse_success_count', 'INTEGER DEFAULT 0');
    ensureColumnSafe(db, 'strategy_library', 'reuse_avg_score', 'REAL DEFAULT 0');
    ensureColumnSafe(db, 'strategy_library', 'reuse_total_duration_ms', 'INTEGER DEFAULT 0');
    ensureColumnSafe(db, 'strategy_library', 'last_reused_at', 'TEXT');

    // 18. 长程目标管理（L3）
    db.exec(`
      CREATE TABLE IF NOT EXISTS agent_goals (
        id TEXT PRIMARY KEY,
        session_id TEXT,
        title TEXT NOT NULL,
        description TEXT,
        acceptance TEXT,
        deadline TEXT,
        status TEXT DEFAULT 'active',
        progress INTEGER DEFAULT 0,
        priority INTEGER DEFAULT 0,
        last_review TEXT,
        last_review_result TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT
      );
      CREATE INDEX IF NOT EXISTS idx_goal_status ON agent_goals(status);
      CREATE INDEX IF NOT EXISTS idx_goal_deadline ON agent_goals(deadline);
    `);
    console.log("[Migration] Created agent_goals table.");

    // 19. 巡检运行日志（L2 可观测性）
    db.exec(`
      CREATE TABLE IF NOT EXISTS patrol_runs (
        id TEXT PRIMARY KEY,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        sessions_scanned INTEGER DEFAULT 0,
        suggestions_created INTEGER DEFAULT 0,
        auto_requeued INTEGER DEFAULT 0,
        note TEXT
      );
      CREATE INDEX IF NOT EXISTS idx_patrol_started ON patrol_runs(started_at);
    `);
    console.log("[Migration] Created patrol_runs table.");

    // 20. 应用级设置（巡检开关 / 间隔等）
    db.exec(`
      CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TEXT NOT NULL
      );
    `);
    console.log("[Migration] Created app_settings table.");

    // 21. 笔记：人类笔记 + AI 可读知识
    db.exec(`
      CREATE TABLE IF NOT EXISTS notes (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        content_md TEXT NOT NULL,
        tags TEXT DEFAULT '[]',
        owner TEXT DEFAULT 'human',
        visibility TEXT DEFAULT 'shared',
        task_id TEXT,
        pinned INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_notes_owner ON notes(owner);
      CREATE INDEX IF NOT EXISTS idx_notes_visibility ON notes(visibility);
      CREATE INDEX IF NOT EXISTS idx_notes_task ON notes(task_id);
      CREATE INDEX IF NOT EXISTS idx_notes_updated ON notes(updated_at);
    `);
    console.log("[Migration] Created notes table.");

    // 22. AI 工作日志：长期自主任务的步骤、决策、产物与未决问题
    db.exec(`
      CREATE TABLE IF NOT EXISTS agent_journal (
        id TEXT PRIMARY KEY,
        task_id TEXT,
        session_id TEXT,
        event_type TEXT NOT NULL,
        summary TEXT NOT NULL,
        details TEXT,
        artifacts TEXT,
        created_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_journal_task ON agent_journal(task_id);
      CREATE INDEX IF NOT EXISTS idx_journal_session ON agent_journal(session_id);
      CREATE INDEX IF NOT EXISTS idx_journal_created ON agent_journal(created_at);
    `);
    console.log("[Migration] Created agent_journal table.");

    // 23. 编码搭档运行记录
    db.exec(`
      CREATE TABLE IF NOT EXISTS coding_runs (
        id TEXT PRIMARY KEY,
        agent_key TEXT NOT NULL,
        goal TEXT NOT NULL,
        cwd TEXT,
        command TEXT,
        model TEXT,
        status TEXT DEFAULT 'running',
        output TEXT,
        exit_code INTEGER,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        note_id TEXT
      );
      CREATE INDEX IF NOT EXISTS idx_coding_started ON coding_runs(started_at);
      CREATE INDEX IF NOT EXISTS idx_coding_status ON coding_runs(status);
    `);
    console.log("[Migration] Created coding_runs table.");

    // 24. 站内通知：后台任务 / 编码任务完成提醒
    db.exec(`
      CREATE TABLE IF NOT EXISTS notifications (
        id TEXT PRIMARY KEY,
        type TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT,
        session_id TEXT,
        link TEXT,
        read INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(read);
      CREATE INDEX IF NOT EXISTS idx_notifications_session ON notifications(session_id, read);
      CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at);
    `);
    console.log("[Migration] Created notifications table.");

    // 25. 群文件
    db.exec(`
      CREATE TABLE IF NOT EXISTS group_files (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        name TEXT NOT NULL,
        stored_path TEXT NOT NULL,
        size INTEGER DEFAULT 0,
        mime TEXT,
        uploaded_by TEXT DEFAULT 'human',
        created_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_group_files_session ON group_files(session_id, created_at);
    `);
    console.log("[Migration] Created group_files table.");

  } catch (err) {
    console.error('[Migration] Failed to migrate database:', err);
  }
}
