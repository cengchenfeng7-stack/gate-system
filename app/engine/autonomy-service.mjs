// 自主性 L2 / L3 服务层
// L2：后台巡检（不依赖用户发消息）、跨会话用户画像、建议反馈权重
// L3：任务队列 + 后台调度 + 重启恢复、长程目标与验收、高分策略沉淀

import { randomUUID } from 'node:crypto';

const CN_STOPWORDS = new Set([
  '我们', '你们', '他们', '什么', '怎么', '可以', '一个', '这个', '那个', '现在', '需要',
  '帮我', '请帮', '我的', '你的', '是不', '不是', '有没', '没有', '一下', '然后', '就是',
  '还是', '这些', '那些', '如何', '为什', '因为', '所以', '但是', '如果', '已经', '时候',
  '问题', '任务', '进行', '使用', '通过', '以及', '并且', '或者', '之后', '之前', '今天',
  '明天', '昨天', '谢谢', '你好', '这个', '那个', '一些', '看看', '帮我', '能不', '不能',
  '的话', '了吗', '是的', '对的', '好的', '知道', '觉得', '希望', '想要', '必须', '应该'
]);

// 与主服务一致的中文二元切分，用于相似度与画像主题
export function tokenize(text) {
  const lower = String(text || '').toLowerCase();
  const terms = new Set();
  for (const w of lower.match(/[a-z0-9]{2,}/g) || []) terms.add(w);
  for (const seg of lower.match(/[\u4e00-\u9fa5]+/g) || []) {
    if (seg.length <= 2) {
      terms.add(seg);
    } else {
      for (let i = 0; i < seg.length - 1; i++) terms.add(seg.slice(i, i + 2));
    }
  }
  return Array.from(terms).filter((t) => t.length >= 2 && !CN_STOPWORDS.has(t) && !/^\d+$/.test(t));
}

function topKeywords(texts, limit = 12) {
  const counts = new Map();
  for (const text of texts) {
    for (const t of tokenize(text)) counts.set(t, (counts.get(t) || 0) + 1);
  }
  return [...counts.entries()]
    .filter(([, c]) => c >= 2)
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([word, count]) => ({ word, count }));
}

function jaccard(a, b) {
  const setA = new Set(a);
  const setB = new Set(b);
  if (setA.size === 0 || setB.size === 0) return 0;
  let inter = 0;
  for (const x of setA) if (setB.has(x)) inter++;
  return inter / (setA.size + setB.size - inter);
}

function safeJson(value, fallback) {
  try {
    const parsed = JSON.parse(value);
    return parsed === null || parsed === undefined ? fallback : parsed;
  } catch {
    return fallback;
  }
}

function nowIso() {
  return new Date().toISOString();
}

// ==================== L2 · 跨会话用户画像 ====================
export class UserProfileService {
  constructor(db) {
    this.db = db;
  }

  rebuild(days = 30) {
    const since = new Date(Date.now() - days * 864e5).toISOString();
    const sessions = this.db.prepare(
      'SELECT id, title FROM sessions WHERE updated_at >= ? ORDER BY updated_at DESC LIMIT 200'
    ).all(since);
    const userMessages = this.db.prepare(
      "SELECT content FROM messages WHERE role = 'user' AND created_at >= ? ORDER BY created_at DESC LIMIT 2000"
    ).all(since);
    const intentRows = this.db.prepare(
      `SELECT COALESCE(intent_name, intent) AS name, COUNT(*) AS c
       FROM intent_logs WHERE created_at >= ? GROUP BY intent ORDER BY c DESC LIMIT 10`
    ).all(since);

    const topicSource = [...sessions.map((s) => s.title || ''), ...userMessages.map((m) => m.content || '')];
    const topics = topKeywords(topicSource, 12);
    const intents = intentRows.map((r) => ({ name: r.name || '未分类', count: r.c }));
    const accepted = this.db.prepare(
      "SELECT COUNT(*) AS c FROM proactive_services WHERE feedback = 'accepted' AND created_at >= ?"
    ).get(since)?.c || 0;
    const dismissed = this.db.prepare(
      "SELECT COUNT(*) AS c FROM proactive_services WHERE feedback = 'dismissed' AND created_at >= ?"
    ).get(since)?.c || 0;

    const summaryParts = [
      `近 ${days} 天共 ${sessions.length} 个会话、${userMessages.length} 条提问`,
      topics.length ? `关注主题：${topics.slice(0, 6).map((t) => t.word).join('、')}` : '',
      intents.length ? `高频意图：${intents.slice(0, 4).map((i) => i.name).join('、')}` : '',
      `主动建议采纳 ${accepted} 次 / 忽略 ${dismissed} 次`
    ].filter(Boolean);

    const row = {
      id: 'default',
      summary: summaryParts.join('；'),
      topics: JSON.stringify(topics),
      intents: JSON.stringify(intents),
      session_count: sessions.length,
      message_count: userMessages.length,
      updated_at: nowIso()
    };
    this.db.prepare(`
      INSERT INTO user_profiles (id, summary, topics, intents, session_count, message_count, updated_at)
      VALUES (@id, @summary, @topics, @intents, @session_count, @message_count, @updated_at)
      ON CONFLICT(id) DO UPDATE SET
        summary = excluded.summary, topics = excluded.topics, intents = excluded.intents,
        session_count = excluded.session_count, message_count = excluded.message_count,
        updated_at = excluded.updated_at
    `).run(row);
    return this.get();
  }

  get() {
    try {
      const row = this.db.prepare('SELECT * FROM user_profiles WHERE id = ?').get('default');
      if (!row) return null;
      return {
        id: row.id,
        summary: row.summary || '',
        topics: safeJson(row.topics, []),
        intents: safeJson(row.intents, []),
        sessionCount: row.session_count || 0,
        messageCount: row.message_count || 0,
        updatedAt: row.updated_at
      };
    } catch {
      return null;
    }
  }

  ensureFresh(maxAgeHours = 6) {
    const profile = this.get();
    if (!profile) return this.rebuild();
    const age = Date.now() - new Date(profile.updatedAt).getTime();
    if (!Number.isFinite(age) || age > maxAgeHours * 3600e3) return this.rebuild();
    return profile;
  }

  // 注入系统提示词：让所有模式都了解跨会话偏好
  buildPromptBlock() {
    try {
      const profile = this.get();
      if (!profile) return '';
      const ageDays = (Date.now() - new Date(profile.updatedAt).getTime()) / 864e5;
      if (!Number.isFinite(ageDays) || ageDays > 14) return '';
      const topics = (profile.topics || []).slice(0, 8).map((t) => t.word).join('、');
      const intents = (profile.intents || []).slice(0, 5).map((t) => t.name).join('、');
      if (!topics && !intents) return '';
      return [
        '【跨会话用户画像 · 供参考，不要直接复述】',
        profile.summary ? `- ${profile.summary}` : '',
        topics ? `- 近期关注：${topics}` : '',
        intents ? `- 常见意图：${intents}` : ''
      ].filter(Boolean).join('\n');
    } catch {
      return '';
    }
  }
}

// ==================== L2 · 主动服务后台巡检 ====================
export class ProactivePatrol {
  constructor(db, { searchFaq = null, profileService = null, intervalMinutes = 30 } = {}) {
    this.db = db;
    this.searchFaq = searchFaq;
    this.profile = profileService || new UserProfileService(db);
    this.defaultIntervalMinutes = intervalMinutes;
    this.timer = null;
    this.running = false;
    this.lastRun = null;
  }

  getSetting(key, fallback) {
    try {
      const row = this.db.prepare('SELECT value FROM app_settings WHERE key = ?').get(key);
      return row ? row.value : fallback;
    } catch {
      return fallback;
    }
  }

  setSetting(key, value) {
    this.db.prepare(`
      INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)
      ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
    `).run(key, String(value), nowIso());
  }

  isEnabled() {
    return String(this.getSetting('patrol_enabled', '1')) !== '0';
  }

  getIntervalMinutes() {
    const n = Number(this.getSetting('patrol_interval_minutes', this.defaultIntervalMinutes));
    return Number.isFinite(n) && n >= 1 ? Math.min(1440, Math.round(n)) : this.defaultIntervalMinutes;
  }

  configure({ enabled, intervalMinutes } = {}) {
    if (enabled !== undefined) this.setSetting('patrol_enabled', enabled ? '1' : '0');
    if (intervalMinutes !== undefined) this.setSetting('patrol_interval_minutes', Math.max(1, Math.min(1440, Number(intervalMinutes) || this.defaultIntervalMinutes)));
    this.stop();
    if (this.isEnabled()) this.start();
    return this.status();
  }

  start() {
    this.stop();
    if (!this.isEnabled()) {
      console.log('[Patrol] 后台巡检已关闭');
      return;
    }
    const intervalMs = this.getIntervalMinutes() * 60e3;
    // 启动后 45 秒跑首轮，之后按间隔
    const first = setTimeout(() => {
      this.runOnce({ trigger: 'startup' }).catch((e) => console.warn('[Patrol] 首轮巡检失败:', e?.message || e));
    }, 45e3);
    if (typeof first.unref === 'function') first.unref();
    this.timer = setInterval(() => {
      this.runOnce({ trigger: 'schedule' }).catch((e) => console.warn('[Patrol] 定时巡检失败:', e?.message || e));
    }, intervalMs);
    if (typeof this.timer.unref === 'function') this.timer.unref();
    console.log(`[Patrol] 后台巡检已启用，每 ${this.getIntervalMinutes()} 分钟一次`);
  }

  stop() {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  status() {
    let last = this.lastRun;
    if (!last) {
      try {
        last = this.db.prepare('SELECT * FROM patrol_runs ORDER BY started_at DESC LIMIT 1').get() || null;
      } catch {
        last = null;
      }
    }
    return {
      enabled: this.isEnabled(),
      intervalMinutes: this.getIntervalMinutes(),
      running: this.running,
      lastRun: last
    };
  }

  feedbackWeight(serviceType, action) {
    try {
      const row = this.db.prepare(`
        SELECT
          SUM(CASE WHEN feedback = 'accepted' THEN 1 ELSE 0 END) AS accepted,
          SUM(CASE WHEN feedback = 'dismissed' THEN 1 ELSE 0 END) AS dismissed
        FROM proactive_services WHERE service_type = ? AND action = ?
      `).get(serviceType, action);
      const accepted = row?.accepted || 0;
      const dismissed = row?.dismissed || 0;
      const value = 1 + (accepted - dismissed * 0.5) / (accepted + dismissed + 1);
      return Number(Math.max(0.3, Math.min(2.5, value)).toFixed(2));
    } catch {
      return 1;
    }
  }

  addSuggestion(sessionId, { type, priority, message, action, data = {}, source = 'patrol' }) {
    if (!sessionId) return 0;
    try {
      const exists = this.db.prepare(`
        SELECT 1 FROM proactive_services
        WHERE session_id = ? AND service_type = ? AND action = ?
          AND (status = 'pending' OR created_at > ?)
        LIMIT 1
      `).get(sessionId, type, action, new Date(Date.now() - 24 * 3600e3).toISOString());
      if (exists) return 0;
      const weight = this.feedbackWeight(type, action);
      this.db.prepare(`
        INSERT INTO proactive_services (id, session_id, service_type, trigger_condition, action, status, priority, weight, created_at)
        VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)
      `).run(
        randomUUID(), sessionId, type,
        JSON.stringify({ message, source, ...data }),
        action,
        priority === 'high' ? 3 : priority === 'medium' ? 2 : 1,
        weight, nowIso()
      );
      return 1;
    } catch (e) {
      console.warn('[Patrol] 建议写入失败(忽略):', e?.message || e);
      return 0;
    }
  }

  analyzeSession(sessionId) {
    let created = 0;
    const messages = this.db.prepare(
      "SELECT content FROM messages WHERE session_id = ? AND role = 'user' ORDER BY created_at DESC LIMIT 20"
    ).all(sessionId).reverse().filter((m) => m.content && m.content.trim());

    // 1) 重复主题聚类（同一问题问过 3 次以上）
    const clusters = [];
    for (const msg of messages) {
      const tokens = tokenize(msg.content);
      if (tokens.length < 2) continue;
      const hit = clusters.find((c) => jaccard(c.tokens, tokens) >= 0.6);
      if (hit) {
        hit.items.push(msg.content.slice(0, 120));
        hit.tokens = [...new Set([...hit.tokens, ...tokens])].slice(0, 40);
      } else {
        clusters.push({ tokens, items: [msg.content.slice(0, 120)] });
      }
    }
    const repeated = clusters.filter((c) => c.items.length >= 3).sort((a, b) => b.items.length - a.items.length)[0];
    if (repeated) {
      created += this.addSuggestion(sessionId, {
        type: 'repetitive_question',
        priority: 'medium',
        message: `后台巡检发现该会话有 ${repeated.items.length} 次相似提问，建议沉淀为知识库条目，后续可直接命中。`,
        action: 'create_knowledge_base',
        data: { questions: repeated.items.slice(0, 3) }
      });
    }

    // 2) 知识缺口：最近提问在知识库中命中度过低
    if (this.searchFaq && messages.length > 0) {
      const last = messages[messages.length - 1].content;
      try {
        const hits = this.searchFaq(last, 1) || [];
        if (hits.length === 0 || Number(hits[0].score || 0) < 0.35) {
          created += this.addSuggestion(sessionId, {
            type: 'knowledge_gap',
            priority: 'low',
            message: `后台巡检发现最近的问题「${last.slice(0, 40)}」在知识库中覆盖不足，建议补充条目。`,
            action: 'create_knowledge_base',
            data: { question: last.slice(0, 200) }
          });
        }
      } catch { /* 检索失败不影响巡检 */ }
    }

    // 3) 长期未推进的跟踪任务
    try {
      const task = this.db.prepare(`
        SELECT id, task_name FROM task_tracking
        WHERE session_id = ? AND status IN ('pending', 'in_progress')
          AND created_at < ?
        ORDER BY created_at ASC LIMIT 1
      `).get(sessionId, new Date(Date.now() - 30 * 60e3).toISOString());
      if (task) {
        created += this.addSuggestion(sessionId, {
          type: 'unfinished_task',
          priority: 'medium',
          message: `任务「${task.task_name}」已超过 30 分钟未推进，需要继续吗？`,
          action: 'resume_task',
          data: { taskId: task.id, taskName: task.task_name }
        });
      }
    } catch { /* 忽略 */ }

    // 4) 暂停的自主任务
    try {
      const paused = this.db.prepare(`
        SELECT id, goal, last_error FROM autonomous_tasks
        WHERE session_id = ? AND status = 'paused'
        ORDER BY updated_at DESC LIMIT 1
      `).get(sessionId);
      if (paused) {
        created += this.addSuggestion(sessionId, {
          type: 'paused_autonomous_task',
          priority: 'high',
          message: `自主任务「${String(paused.goal).slice(0, 40)}」处于暂停状态${paused.last_error ? `（${String(paused.last_error).slice(0, 60)}）` : ''}，可续跑或等待自动重试。`,
          action: 'resume_task',
          data: { autonomousTaskId: paused.id }
        });
      }
    } catch { /* 忽略 */ }

    return created;
  }

  checkGoals() {
    let created = 0;
    try {
      const goals = this.db.prepare(`
        SELECT * FROM agent_goals
        WHERE status = 'active' AND deadline IS NOT NULL AND session_id IS NOT NULL
          AND deadline < ? LIMIT 10
      `).all(new Date(Date.now() + 24 * 3600e3).toISOString());
      for (const goal of goals) {
        const overdue = new Date(goal.deadline).getTime() < Date.now();
        created += this.addSuggestion(goal.session_id, {
          type: overdue ? 'goal_overdue' : 'goal_due_soon',
          priority: 'high',
          message: `长程目标「${goal.title}」${overdue ? '已逾期' : '将在 24 小时内到期'}，当前进度 ${goal.progress}%。`,
          action: 'review_goal',
          data: { goalId: goal.id }
        });
      }
    } catch { /* 忽略 */ }
    return created;
  }

  autoRequeuePaused() {
    let count = 0;
    try {
      const rows = this.db.prepare(`
        SELECT id, auto_retry FROM autonomous_tasks
        WHERE status = 'paused' AND auto_retry < COALESCE(max_retry, 2) AND last_error IS NOT NULL
          AND updated_at < ?
        ORDER BY updated_at ASC LIMIT 3
      `).all(new Date(Date.now() - 10 * 60e3).toISOString());
      for (const row of rows) {
        this.db.prepare(`
          UPDATE autonomous_tasks
          SET status = 'queued', auto_retry = auto_retry + 1, updated_at = ?
          WHERE id = ? AND status = 'paused'
        `).run(nowIso(), row.id);
        count++;
      }
    } catch (e) {
      console.warn('[Patrol] 自动重试入队失败(忽略):', e?.message || e);
    }
    return count;
  }

  async runOnce({ trigger = 'manual' } = {}) {
    if (this.running) return { skipped: true, reason: '巡检已在运行' };
    this.running = true;
    const startedAt = nowIso();
    let sessionsScanned = 0;
    let suggestions = 0;
    let requeued = 0;
    let note = '';
    try {
      const sessions = this.db.prepare(
        'SELECT id FROM sessions WHERE updated_at >= ? ORDER BY updated_at DESC LIMIT 30'
      ).all(new Date(Date.now() - 7 * 864e5).toISOString());
      for (const s of sessions) {
        sessionsScanned++;
        try { suggestions += this.analyzeSession(s.id); } catch { /* 单会话失败不影响整体 */ }
      }
      suggestions += this.checkGoals();
      requeued = this.autoRequeuePaused();
      try { this.profile.ensureFresh(); } catch { /* 画像失败不影响巡检 */ }
      this.lastRun = {
        id: randomUUID(),
        started_at: startedAt,
        finished_at: nowIso(),
        sessions_scanned: sessionsScanned,
        suggestions_created: suggestions,
        auto_requeued: requeued,
        note: trigger
      };
      this.db.prepare(`
        INSERT INTO patrol_runs (id, started_at, finished_at, sessions_scanned, suggestions_created, auto_requeued, note)
        VALUES (?, ?, ?, ?, ?, ?, ?)
      `).run(
        this.lastRun.id, this.lastRun.started_at, this.lastRun.finished_at,
        sessionsScanned, suggestions, requeued, trigger
      );
      if (suggestions > 0 || requeued > 0) {
        console.log(`[Patrol] 巡检完成：扫描 ${sessionsScanned} 个会话，新增建议 ${suggestions} 条，自动重新入队 ${requeued} 个任务`);
      }
      return this.lastRun;
    } catch (e) {
      note = e?.message || String(e);
      console.warn('[Patrol] 巡检异常:', note);
      return { error: note };
    } finally {
      this.running = false;
    }
  }
}

// ==================== L3 · 策略库（高分路径沉淀 + 效果回归） ====================
export class StrategyLibrary {
  constructor(db) {
    this.db = db;
  }

  list(limit = 50) {
    const rows = this.db.prepare(
      'SELECT * FROM strategy_library ORDER BY avg_score DESC, use_count DESC, updated_at DESC LIMIT ?'
    ).all(limit);
    return rows.map((r) => ({
      id: r.id,
      name: r.name,
      goalPattern: r.goal_pattern,
      keywords: safeJson(r.keywords, []),
      steps: safeJson(r.steps, []),
      avgScore: Number(r.avg_score || 0),
      useCount: r.use_count || 0,
      sourceTask: r.source_task,
      createdAt: r.created_at,
      updatedAt: r.updated_at,
      // v2.9 效果回归字段
      reuseCount: r.reuse_count || 0,
      reuseSuccessCount: r.reuse_success_count || 0,
      reuseAvgScore: Number(r.reuse_avg_score || 0),
      reuseTotalDurationMs: r.reuse_total_duration_ms || 0,
      lastReusedAt: r.last_reused_at
    }));
  }

  remove(id) {
    return this.db.prepare('DELETE FROM strategy_library WHERE id = ?').run(id).changes > 0;
  }

  // 按目标关键词相似度匹配，命中后累计使用次数，并记录复用开始时间（用于后续统计耗时）
  matchForGoal(goal, limit = 2) {
    try {
      const goalTokens = tokenize(goal);
      const rows = this.db.prepare('SELECT * FROM strategy_library').all();
      const scored = rows.map((r) => {
        const keywords = safeJson(r.keywords, []);
        return { row: r, score: jaccard(goalTokens, keywords) * 0.7 + Number(r.avg_score || 0) * 0.3 };
      }).filter((x) => x.score >= 0.25)
        .sort((a, b) => b.score - a.score)
        .slice(0, limit);
      const now = nowIso();
      for (const item of scored) {
        this.db.prepare('UPDATE strategy_library SET use_count = use_count + 1, updated_at = ? WHERE id = ?')
          .run(now, item.row.id);
      }
      return scored.map((s) => ({
        id: s.row.id,
        name: s.row.name,
        steps: safeJson(s.row.steps, []),
        avgScore: Number(s.row.avg_score || 0)
      }));
    } catch {
      return [];
    }
  }

  // 记录策略复用后的执行结果（由任务完成时调用）
  // 参数：strategyId, success(boolean), score(0-1), durationMs
  recordReuseResult(strategyId, { success, score, durationMs }) {
    try {
      const now = nowIso();
      const row = this.db.prepare('SELECT * FROM strategy_library WHERE id = ?').get(strategyId);
      if (!row) return false;
      
      const reuseCount = (row.reuse_count || 0) + 1;
      const reuseSuccessCount = (row.reuse_success_count || 0) + (success ? 1 : 0);
      const reuseTotalDurationMs = (row.reuse_total_duration_ms || 0) + (durationMs || 0);
      const reuseAvgScore = reuseSuccessCount > 0
        ? ((Number(row.reuse_avg_score || 0) * (row.reuse_success_count || 0)) + (success ? score : 0)) / reuseSuccessCount
        : 0;

      this.db.prepare(`
        UPDATE strategy_library
        SET reuse_count = ?, reuse_success_count = ?, reuse_avg_score = ?, reuse_total_duration_ms = ?, last_reused_at = ?, updated_at = ?
        WHERE id = ?
      `).run(reuseCount, reuseSuccessCount, reuseAvgScore, reuseTotalDurationMs, now, now, strategyId);
      return true;
    } catch (e) {
      console.warn('[Strategy] 记录复用结果失败:', e?.message || e);
      return false;
    }
  }

  // 评估所有策略效果，自动淘汰低效策略
  // 规则：reuse_count >= 10 且 reuse_success_count / reuse_count < 0.3（成功率<30%）或 reuse_avg_score < 0.5
  // 返回：{ removed: number, kept: number, details: [...] }
  evaluateAndPrune(minReuseCount = 10, minSuccessRate = 0.3, minAvgScore = 0.5) {
    try {
      const rows = this.db.prepare('SELECT * FROM strategy_library').all();
      const details = [];
      let removed = 0;
      for (const row of rows) {
        const reuseCount = row.reuse_count || 0;
        const reuseSuccessCount = row.reuse_success_count || 0;
        const reuseAvgScore = Number(row.reuse_avg_score || 0);
        
        if (reuseCount >= minReuseCount) {
          const successRate = reuseCount > 0 ? reuseSuccessCount / reuseCount : 0;
          const shouldRemove = successRate < minSuccessRate || reuseAvgScore < minAvgScore;
          
          if (shouldRemove) {
            this.db.prepare('DELETE FROM strategy_library WHERE id = ?').run(row.id);
            details.push({
              id: row.id,
              name: row.name,
              action: 'removed',
              reason: `复用${reuseCount}次，成功率${(successRate * 100).toFixed(1)}%，复用均分${reuseAvgScore.toFixed(2)}`
            });
            removed++;
          } else {
            details.push({
              id: row.id,
              name: row.name,
              action: 'kept',
              reason: `复用${reuseCount}次，成功率${(successRate * 100).toFixed(1)}%，复用均分${reuseAvgScore.toFixed(2)}`
            });
          }
        } else {
          details.push({
            id: row.id,
            name: row.name,
            action: 'kept',
            reason: `复用次数${reuseCount} < ${minReuseCount}，暂不评估`
          });
        }
      }
      return { removed, kept: rows.length - removed, details };
    } catch (e) {
      console.warn('[Strategy] 策略效果评估失败:', e?.message || e);
      return { removed: 0, kept: 0, details: [], error: e?.message };
    }
  }

  // 手动触发策略效果评估（保留兼容）
  evaluate() {
    return this.evaluateAndPrune();
  }

  // 任务完成后沉淀：2 步以上且平均评分 >= 0.8
  captureFromTask(taskId) {
    try {
      const task = this.db.prepare('SELECT * FROM autonomous_tasks WHERE id = ?').get(taskId);
      if (!task) return null;
      const plan = safeJson(task.plan, []);
      const results = safeJson(task.step_results, []).filter(Boolean);
      if (!Array.isArray(plan) || plan.length < 2 || results.length === 0) return null;
      const scores = results.map((r) => Number(r.score || 0));
      const avg = scores.reduce((a, b) => a + b, 0) / scores.length;
      if (avg < 0.8) return null;

      const keywords = tokenize(task.goal).slice(0, 12);
      const exists = this.db.prepare('SELECT * FROM strategy_library').all()
        .find((r) => jaccard(safeJson(r.keywords, []), keywords) >= 0.6);
      const now = nowIso();
      if (exists) {
        // 已有同类策略：保留更高分版本，并累计使用次数
        if (avg > Number(exists.avg_score || 0)) {
          this.db.prepare('UPDATE strategy_library SET steps = ?, avg_score = ?, goal_pattern = ?, source_task = ?, updated_at = ? WHERE id = ?')
            .run(JSON.stringify(plan), avg, String(task.goal).slice(0, 200), taskId, now, exists.id);
        } else {
          this.db.prepare('UPDATE strategy_library SET use_count = use_count + 1, updated_at = ? WHERE id = ?')
            .run(now, exists.id);
        }
        return { id: exists.id, updated: true, avgScore: avg, name: exists.name };
      }

      const id = `st-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      this.db.prepare(`
        INSERT INTO strategy_library (id, name, goal_pattern, keywords, steps, avg_score, use_count, source_task, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
      `).run(
        id,
        String(task.goal).slice(0, 40),
        String(task.goal).slice(0, 200),
        JSON.stringify(keywords),
        JSON.stringify(plan),
        avg, taskId, now, now
      );
      return { id, created: true, avgScore: avg, name: String(task.goal).slice(0, 40) };
    } catch (e) {
      console.warn('[Strategy] 策略沉淀失败(忽略):', e?.message || e);
      return null;
    }
  }
}

// ==================== L3 · 长程目标与验收 ====================
export class GoalService {
  constructor(db, { llm = null } = {}) {
    this.db = db;
    this.llm = llm;
  }

  list(status = null) {
    const rows = status
      ? this.db.prepare('SELECT * FROM agent_goals WHERE status = ? ORDER BY created_at DESC LIMIT 100').all(status)
      : this.db.prepare('SELECT * FROM agent_goals ORDER BY created_at DESC LIMIT 100').all();
    return rows.map((r) => ({
      id: r.id,
      sessionId: r.session_id,
      title: r.title,
      description: r.description,
      acceptance: r.acceptance,
      deadline: r.deadline,
      status: r.status,
      progress: r.progress || 0,
      priority: r.priority || 0,
      lastReview: r.last_review,
      lastReviewResult: safeJson(r.last_review_result, null),
      createdAt: r.created_at,
      updatedAt: r.updated_at,
      completedAt: r.completed_at
    }));
  }

  get(id) {
    return this.db.prepare('SELECT * FROM agent_goals WHERE id = ?').get(id);
  }

  create({ title, description = '', acceptance = '', deadline = null, sessionId = null, priority = 0 }) {
    if (!title) throw new Error('目标标题必填');
    const id = `goal-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const now = nowIso();
    this.db.prepare(`
      INSERT INTO agent_goals (id, session_id, title, description, acceptance, deadline, status, progress, priority, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, 'active', 0, ?, ?, ?)
    `).run(id, sessionId, title, description, acceptance, deadline, Number(priority) || 0, now, now);
    return this.get(id);
  }

  update(id, fields = {}) {
    const allowed = ['title', 'description', 'acceptance', 'deadline', 'status', 'progress', 'priority'];
    const sets = [];
    const values = [];
    for (const key of allowed) {
      if (fields[key] !== undefined) {
        sets.push(`${key} = ?`);
        values.push(fields[key]);
      }
    }
    if (sets.length === 0) return this.get(id);
    sets.push('updated_at = ?');
    values.push(nowIso());
    if (fields.status === 'completed') {
      sets.push('completed_at = ?');
      values.push(nowIso());
    }
    values.push(id);
    this.db.prepare(`UPDATE agent_goals SET ${sets.join(', ')} WHERE id = ?`).run(...values);
    return this.get(id);
  }

  remove(id) {
    return this.db.prepare('DELETE FROM agent_goals WHERE id = ?').run(id).changes > 0;
  }

  linkedTasks(goalId) {
    return this.db.prepare('SELECT * FROM autonomous_tasks WHERE goal_id = ? ORDER BY created_at ASC').all(goalId);
  }

  // 依据关联任务重算进度（不做模型验收）
  refreshProgress(goalId) {
    const goal = this.get(goalId);
    if (!goal) return null;
    const tasks = this.linkedTasks(goalId);
    if (tasks.length === 0) return { progress: goal.progress || 0, tasks: 0 };
    let sum = 0;
    for (const t of tasks) {
      if (t.status === 'completed') sum += 1;
      else if (t.status === 'running' || t.status === 'paused' || t.status === 'queued') {
        sum += t.total_steps ? Math.min(0.9, (t.current_step || 0) / t.total_steps) : 0.05;
      }
    }
    const progress = Math.round((sum / tasks.length) * 100);
    this.db.prepare('UPDATE agent_goals SET progress = ?, updated_at = ? WHERE id = ?')
      .run(progress, nowIso(), goalId);
    return { progress, tasks: tasks.length };
  }

  // 验收：有验收标准时交给模型判定；否则按任务完成度判定
  async review(goalId) {
    const goal = this.get(goalId);
    if (!goal) throw new Error('目标不存在');
    const tasks = this.linkedTasks(goalId);
    const progressInfo = this.refreshProgress(goalId);
    const evidence = tasks.map((t) => {
      const results = safeJson(t.step_results, []).filter(Boolean);
      return {
        goal: t.goal,
        status: t.status,
        steps: results.map((r) => ({ name: r.name, verified: !!r.verified, score: r.score, output: String(r.output || '').slice(0, 160) }))
      };
    });

    let verdict = null;
    if (this.llm && goal.acceptance && tasks.length > 0) {
      try {
        const text = await this.llm({
          system: '你是严格但务实的验收员。只输出 JSON，不要输出其他文字。',
          prompt:
            `请根据验收标准判断长程目标是否达成。\n` +
            `目标：${goal.title}\n描述：${goal.description || '无'}\n` +
            `验收标准：${goal.acceptance}\n进度：${progressInfo.progress}%\n` +
            `执行证据：${JSON.stringify(evidence).slice(0, 4000)}\n\n` +
            `只输出 JSON：{"passed":true|false,"reason":"一句话依据","progress":0-100}`
        });
        const raw = String(text || '');
        const jsonText = raw.slice(raw.indexOf('{'), raw.lastIndexOf('}') + 1);
        verdict = JSON.parse(jsonText);
      } catch (e) {
        verdict = { passed: false, reason: `模型验收失败：${e?.message || e}`, progress: progressInfo.progress };
      }
    }
    if (!verdict) {
      const allDone = tasks.length > 0 && tasks.every((t) => t.status === 'completed');
      verdict = {
        passed: Boolean(allDone || progressInfo.progress >= 100),
        reason: allDone ? '关联任务全部完成' : `进度 ${progressInfo.progress}%，任务尚未全部完成`,
        progress: progressInfo.progress
      };
    }

    const now = nowIso();
    const passed = verdict.passed === true;
    this.db.prepare(`
      UPDATE agent_goals
      SET progress = ?, last_review = ?, last_review_result = ?, updated_at = ?
        ${passed ? ", status = 'completed', completed_at = ?" : ''}
      WHERE id = ?
    `).run(
      ...[
        Math.max(0, Math.min(100, Number(verdict.progress ?? progressInfo.progress) || 0)),
        now,
        JSON.stringify({ passed, reason: verdict.reason || '' }),
        now,
        ...(passed ? [now] : []),
        goalId
      ]
    );
    return { ...this.list().find((g) => g.id === goalId), verdict: { passed, reason: verdict.reason || '' } };
  }
}

// ==================== L3 · 任务队列与后台调度 ====================
// 计算循环任务的下一次执行时间（每天 / 每周 / 每月），按本地时区
export function computeNextRun(task, after = new Date()) {
  const recurrence = String(task?.recurrence || 'none');
  if (!['daily', 'weekly', 'monthly'].includes(recurrence)) return null;
  const [rawHour, rawMinute] = String(task?.recur_time || '09:00').split(':').map((n) => Number(n));
  const hour = Number.isFinite(rawHour) ? Math.min(Math.max(rawHour, 0), 23) : 9;
  const minute = Number.isFinite(rawMinute) ? Math.min(Math.max(rawMinute, 0), 59) : 0;
  const base = new Date(after);
  const candidate = new Date(base);
  candidate.setHours(hour, minute, 0, 0);
  if (recurrence === 'daily') {
    if (candidate <= base) candidate.setDate(candidate.getDate() + 1);
  } else if (recurrence === 'weekly') {
    const target = Math.min(Math.max(Number(task?.recur_weekday) || 0, 0), 6);
    let delta = (target - candidate.getDay() + 7) % 7;
    if (delta === 0 && candidate <= base) delta = 7;
    candidate.setDate(candidate.getDate() + delta);
  } else {
    const day = Math.min(Math.max(Number(task?.recur_monthday) || 1, 1), 31);
    const daysInMonth = (y, m) => new Date(y, m + 1, 0).getDate();
    const y = candidate.getFullYear();
    const m = candidate.getMonth();
    candidate.setDate(Math.min(day, daysInMonth(y, m)));
    if (candidate <= base) {
      const nm = m + 1;
      candidate.setFullYear(y, nm, Math.min(day, daysInMonth(y, nm)));
      candidate.setHours(hour, minute, 0, 0);
    }
  }
  return candidate.toISOString();
}

export class TaskQueueService {
  constructor(db, { executeTask, intervalMs = 30e3, onTaskFinished = null } = {}) {
    this.db = db;
    this.executeTask = executeTask;
    this.intervalMs = intervalMs;
    this.onTaskFinished = onTaskFinished;
    this.timer = null;
    this.runningTaskId = null;
  }

  enqueue({
    sessionId, goal, priority = 0, dueAt = null, goalId = null, model = null,
    allowWrites = 1, origin = 'queue', recurrence = 'none', recurTime = '09:00',
    recurWeekday = 1, recurMonthday = 1, maxRetry = 2, timeoutSec = 900
  }) {
    if (!goal) throw new Error('任务目标必填');
    const id = `at-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const now = nowIso();
    this.db.prepare(`
      INSERT INTO autonomous_tasks (
        id, session_id, goal, plan, step_results, total_steps, current_step, status, last_error,
        priority, due_at, goal_id, model, allow_writes, replan_count, auto_retry, origin,
        recurrence, recur_time, recur_weekday, recur_monthday, max_retry, timeout_sec,
        created_at, updated_at, completed_at
      ) VALUES (?, ?, ?, NULL, ?, 0, 0, 'queued', NULL, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
    `).run(
      id, sessionId || null, goal, JSON.stringify([]), Number(priority) || 0,
      dueAt, goalId, model, allowWrites ? 1 : 0, origin,
      recurrence, recurTime, Number(recurWeekday) || 0, Number(recurMonthday) || 1,
      Number(maxRetry) || 0, Number(timeoutSec) || 900, now, now
    );
    return this.db.prepare('SELECT * FROM autonomous_tasks WHERE id = ?').get(id);
  }

  requeue(taskId) {
    const r = this.db.prepare(`
      UPDATE autonomous_tasks
      SET status = 'queued', last_error = NULL, updated_at = ?
      WHERE id = ? AND status IN ('paused', 'running', 'queued')
    `).run(nowIso(), taskId);
    return r.changes > 0;
  }

  // 服务重启：把中断的 running 任务重新放回队列，按检查点续跑
  recoverOnStart() {
    try {
      const r = this.db.prepare(`
        UPDATE autonomous_tasks
        SET status = 'queued', last_error = COALESCE(last_error, '服务重启自动恢复'),
            started_at = NULL, updated_at = ?
        WHERE status = 'running'
      `).run(nowIso());
      return r.changes || 0;
    } catch (e) {
      console.warn('[Queue] 启动恢复失败(忽略):', e?.message || e);
      return 0;
    }
  }

  // 循环任务：重置检查点并安排下一次执行
  rescheduleRecurring(task) {
    const next = computeNextRun(task, new Date());
    if (!next) return null;
    this.db.prepare(`
      UPDATE autonomous_tasks
      SET status = 'queued', due_at = ?, plan = NULL, step_results = '[]',
          total_steps = 0, current_step = 0, last_error = NULL, replan_count = 0,
          auto_retry = 0, started_at = NULL, completed_at = NULL, updated_at = ?
      WHERE id = ?
    `).run(next, nowIso(), task.id);
    return next;
  }

  start() {
    this.stop();
    this.timer = setInterval(() => {
      this.tick().catch((e) => console.warn('[Queue] 调度异常:', e?.message || e));
    }, this.intervalMs);
    if (typeof this.timer.unref === 'function') this.timer.unref();
    // 启动后 20 秒先跑一次，处理重启恢复/排队任务
    setTimeout(() => {
      this.tick().catch((e) => console.warn('[Queue] 首轮调度失败:', e?.message || e));
    }, 20e3).unref?.();
    console.log(`[Queue] 后台任务调度已启用，每 ${Math.round(this.intervalMs / 1000)} 秒检查一次`);
  }

  stop() {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  status() {
    let counts = {};
    try {
      const rows = this.db.prepare('SELECT status, COUNT(*) AS c FROM autonomous_tasks GROUP BY status').all();
      counts = Object.fromEntries(rows.map((r) => [r.status, r.c]));
    } catch { /* 忽略 */ }
    return {
      runningTaskId: this.runningTaskId,
      queued: counts.queued || 0,
      running: counts.running || 0,
      paused: counts.paused || 0,
      completed: counts.completed || 0,
      failed: counts.failed || 0
    };
  }

  async tick() {
    if (this.runningTaskId || typeof this.executeTask !== 'function') return null;
    const task = this.db.prepare(`
      SELECT * FROM autonomous_tasks
      WHERE status = 'queued' AND (due_at IS NULL OR due_at <= ?)
      ORDER BY priority DESC, created_at ASC LIMIT 1
    `).get(nowIso());
    if (!task) return null;

    this.runningTaskId = task.id;
    this.db.prepare(`
      UPDATE autonomous_tasks SET status = 'running', started_at = ?, updated_at = ?
      WHERE id = ?
    `).run(nowIso(), nowIso(), task.id);

    try {
      await this.executeTask(task);
    } catch (e) {
      this.db.prepare(`
        UPDATE autonomous_tasks SET status = 'paused', last_error = ?, updated_at = ? WHERE id = ?
      `).run(e?.message || String(e), nowIso(), task.id);
    } finally {
      let after = null;
      try {
        after = this.db.prepare('SELECT * FROM autonomous_tasks WHERE id = ?').get(task.id);
        if (after && after.status === 'running') {
          this.db.prepare(`
            UPDATE autonomous_tasks SET status = 'paused', last_error = COALESCE(last_error, '执行未正常结束'), updated_at = ?
            WHERE id = ?
          `).run(nowIso(), task.id);
          after = this.db.prepare('SELECT * FROM autonomous_tasks WHERE id = ?').get(task.id);
        }
        if (after) {
          const runStatus = after.status;
          // 失败自动重试：指数退避，默认最多 2 次
          if (runStatus === 'paused') {
            const retries = Number(after.auto_retry) || 0;
            const maxRetryRaw = Number(after.max_retry);
            const maxRetry = Number.isFinite(maxRetryRaw) ? maxRetryRaw : 2;
            if (retries < maxRetry) {
              const delayMs = Math.min(60_000 * (retries + 1), 300_000);
              this.db.prepare(`
                UPDATE autonomous_tasks SET status = 'queued', auto_retry = auto_retry + 1,
                  due_at = ?, started_at = NULL, updated_at = ? WHERE id = ?
              `).run(new Date(Date.now() + delayMs).toISOString(), nowIso(), task.id);
              after = this.db.prepare('SELECT * FROM autonomous_tasks WHERE id = ?').get(task.id);
              after._autoRetry = true;
            } else if (String(after.recurrence || 'none') !== 'none') {
              const next = this.rescheduleRecurring(after);
              if (next) {
                after = this.db.prepare('SELECT * FROM autonomous_tasks WHERE id = ?').get(task.id);
                after._rescheduledTo = next;
              }
            }
          } else if (runStatus === 'completed' && String(after.recurrence || 'none') !== 'none') {
            const next = this.rescheduleRecurring(after);
            if (next) {
              after = this.db.prepare('SELECT * FROM autonomous_tasks WHERE id = ?').get(task.id);
              after._rescheduledTo = next;
            }
          }
          after._runStatus = runStatus;
        }
      } catch (e) {
        console.warn('[Queue] 任务收尾失败(忽略):', e?.message || e);
      }
      this.runningTaskId = null;
      if (after && typeof this.onTaskFinished === 'function') {
        try { this.onTaskFinished(after); } catch { /* 忽略 */ }
      }
    }
    return task.id;
  }
}

export default {
  tokenize,
  UserProfileService,
  ProactivePatrol,
  StrategyLibrary,
  GoalService,
  TaskQueueService
};
