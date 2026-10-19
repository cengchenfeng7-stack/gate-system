// 提示词配置 + 智力强度档位
// 提示词三类：system（系统/角色约束）、step（步骤执行约束）、style（风格+人设）
// 智力六档：1 极简 ~ 6 极致，影响工具轮数、温度、规划粒度与消耗

export const INTELLIGENCE_LEVELS = [
  { level: 1, name: '极简',  maxTurns: 1, temperature: 0.2, planSteps: 1, desc: '单轮直答，不做工具循环，最省 Token' },
  { level: 2, name: '轻量',  maxTurns: 2, temperature: 0.3, planSteps: 2, desc: '允许 1 次工具调用，适合简单查询' },
  { level: 3, name: '标准',  maxTurns: 3, temperature: 0.5, planSteps: 3, desc: '常规工具循环，日常任务默认' },
  { level: 4, name: '增强',  maxTurns: 5, temperature: 0.6, planSteps: 4, desc: '多轮工具+自我校验，复杂任务' },
  { level: 5, name: '深入',  maxTurns: 8, temperature: 0.7, planSteps: 5, desc: '深度推理，允许反复试错' },
  { level: 6, name: '极致',  maxTurns: 12, temperature: 0.8, planSteps: 6, desc: '最大循环+最多步骤，最费 Token' }
];

export function getIntelligenceLevel(n) {
  const level = Number(n);
  return INTELLIGENCE_LEVELS.find(l => l.level === level) || INTELLIGENCE_LEVELS[2];
}

// 默认提示词模板
export const DEFAULT_PROMPTS = {
  global: {
    system_prompt: '',
    step_prompt: '',
    style_prompt: '',
    enforce: 0
  }
};

export class PromptConfigStore {
  constructor(db) {
    this.db = db;
  }

  ensureDefault(scope = 'global') {
    const row = this.db.prepare('SELECT * FROM prompt_config WHERE scope = ?').get(scope);
    if (row) return row;
    const now = new Date().toISOString();
    this.db.prepare(
      'INSERT INTO prompt_config (id, scope, system_prompt, step_prompt, style_prompt, enforce, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)'
    ).run(`pc-${scope}`, scope, '', '', '', 0, now);
    return this.db.prepare('SELECT * FROM prompt_config WHERE scope = ?').get(scope);
  }

  get(scope = 'global') {
    return this.ensureDefault(scope);
  }

  save(scope, { system_prompt, step_prompt, style_prompt, enforce }) {
    this.ensureDefault(scope);
    const fields = [];
    const values = [];
    if (system_prompt !== undefined) { fields.push('system_prompt = ?'); values.push(String(system_prompt)); }
    if (step_prompt !== undefined) { fields.push('step_prompt = ?'); values.push(String(step_prompt)); }
    if (style_prompt !== undefined) { fields.push('style_prompt = ?'); values.push(String(style_prompt)); }
    if (enforce !== undefined) { fields.push('enforce = ?'); values.push(enforce ? 1 : 0); }
    if (fields.length === 0) return false;
    fields.push('updated_at = ?');
    values.push(new Date().toISOString(), scope);
    this.db.prepare(`UPDATE prompt_config SET ${fields.join(', ')} WHERE scope = ?`).run(...values);
    return true;
  }

  // 组装最终 systemPrompt：强制模式下提示词置顶并加硬约束语
  buildSystemPrompt(agentPrompt = '', scope = 'global') {
    const cfg = this.get(scope);
    const parts = [];
    if (cfg.system_prompt && String(cfg.system_prompt).trim()) {
      if (cfg.enforce) {
        parts.push(`【硬性系统约束 · 必须严格遵守，不得违反】\n${cfg.system_prompt.trim()}`);
      } else {
        parts.push(`【系统指令】\n${cfg.system_prompt.trim()}`);
      }
    }
    if (cfg.style_prompt && String(cfg.style_prompt).trim()) {
      if (cfg.enforce) {
        parts.push(`【硬性风格与人设 · 必须始终保持】\n${cfg.style_prompt.trim()}`);
      } else {
        parts.push(`【风格与人设】\n${cfg.style_prompt.trim()}`);
      }
    }
    // 原 Agent 自身人设放在提示词之后，避免覆盖用户强制设置
    if (agentPrompt && String(agentPrompt).trim()) parts.push(String(agentPrompt).trim());
    return parts.join('\n\n');
  }

  // 步骤提示词前缀
  buildStepPrefix(scope = 'global') {
    const cfg = this.get(scope);
    if (!cfg.step_prompt || !String(cfg.step_prompt).trim()) return '';
    return cfg.enforce
      ? `【硬性步骤约束 · 每步必须遵守】\n${cfg.step_prompt.trim()}\n\n`
      : `【步骤指引】\n${cfg.step_prompt.trim()}\n\n`;
  }
}

// 审计日志写入
export class AuditLogger {
  constructor(db) {
    this.db = db;
  }

  log({ sessionId, agentId, agentName, mode, toolName, toolInput, toolOutput, status, durationMs }) {
    try {
      this.db.prepare(`
        INSERT INTO audit_logs (id, session_id, agent_id, agent_name, mode, tool_name, tool_input, tool_output, status, duration_ms, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `).run(
        `al-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        sessionId || null,
        agentId || null,
        agentName || null,
        mode || null,
        String(toolName || 'unknown'),
        this.clip(toolInput),
        this.clip(toolOutput),
        status || 'completed',
        Number(durationMs) || 0,
        new Date().toISOString()
      );
    } catch (e) {
      // 审计失败绝不影响主流程
      console.warn('[Audit] 写入失败(忽略):', e?.message || e);
    }
  }

  clip(v, max = 2000) {
    if (v === undefined || v === null) return null;
    let s;
    try { s = typeof v === 'string' ? v : JSON.stringify(v); } catch { s = String(v); }
    return s.length > max ? s.slice(0, max) + '…(截断)' : s;
  }

  query({ sessionId, status, limit = 200, offset = 0 }) {
    const conditions = [];
    const values = [];
    if (sessionId) { conditions.push('session_id = ?'); values.push(sessionId); }
    if (status) { conditions.push('status = ?'); values.push(status); }
    const where = conditions.length ? `WHERE ${conditions.join(' AND ')}` : '';
    return this.db.prepare(
      `SELECT * FROM audit_logs ${where} ORDER BY created_at DESC LIMIT ? OFFSET ?`
    ).all(...values, Number(limit) || 200, Number(offset) || 0);
  }

  // 汇总：真实执行 vs 空转
  summary(sessionId = null) {
    const where = sessionId ? 'WHERE session_id = ?' : '';
    const params = sessionId ? [sessionId] : [];
    const total = this.db.prepare(`SELECT COUNT(*) AS c FROM audit_logs ${where}`).get(...params)?.c || 0;
    const byStatus = this.db.prepare(
      `SELECT status, COUNT(*) AS c FROM audit_logs ${where} GROUP BY status`
    ).all(...params);
    const byTool = this.db.prepare(
      `SELECT tool_name, COUNT(*) AS c, SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS ok
       FROM audit_logs ${where} GROUP BY tool_name ORDER BY c DESC`
    ).all(...params);
    const writeTools = ['write_file', 'edit_file', 'execute_command', 'add_knowledge'];
    const realWrites = this.db.prepare(
      `SELECT COUNT(*) AS c FROM audit_logs ${where ? where + ' AND' : 'WHERE'} tool_name IN (${writeTools.map(() => '?').join(',')}) AND status = 'completed'`
    ).get(...params, ...writeTools)?.c || 0;
    return { total, byStatus, byTool, realWrites };
  }
}

export default { INTELLIGENCE_LEVELS, getIntelligenceLevel, DEFAULT_PROMPTS, PromptConfigStore, AuditLogger };