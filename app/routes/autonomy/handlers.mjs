import {
  computeNextRun,
  db,
  goalService,
  mapAutonomousTask,
  nowIso,
  proactivePatrol,
  strategyLibrary,
  taskQueue,
  userProfileService
} from "../../server.mjs";
// 自主性 L2/L3 路由处理函数

export const autonomyHandlers = {
  async getAutonomyStatus(req, res) {
    try {
      const profile = userProfileService.get();
      res.json({
        success: true,
        patrol: proactivePatrol.status(),
        queue: taskQueue.status(),
        profile: profile ? { summary: profile.summary, topics: profile.topics, intents: profile.intents, updatedAt: profile.updatedAt } : null,
        strategyCount: db.prepare("SELECT COUNT(*) AS c FROM strategy_library").get()?.c || 0,
        goalActive: db.prepare("SELECT COUNT(*) AS c FROM agent_goals WHERE status = 'active'").get()?.c || 0
      });
    } catch (e) {
      res.status(500).json({ error: e?.message || "读取失败" });
    }
  },

  async runPatrol(req, res) {
    try {
      const result = await proactivePatrol.runOnce({ trigger: "manual" });
      res.json({ success: !result?.error, result });
    } catch (e) {
      res.status(500).json({ error: e?.message || "巡检失败" });
    }
  },

  async configurePatrol(req, res) {
    try {
      const { enabled, intervalMinutes } = req.body || {};
      const status = proactivePatrol.configure({
        enabled: enabled === undefined ? undefined : Boolean(enabled),
        intervalMinutes: intervalMinutes === undefined ? undefined : Number(intervalMinutes)
      });
      res.json({ success: true, ...status });
    } catch (e) {
      res.status(500).json({ error: e?.message || "配置失败" });
    }
  },

  async getProfile(req, res) {
    try {
      res.json({ success: true, profile: userProfileService.get() });
    } catch (e) {
      res.status(500).json({ error: e?.message || "读取失败" });
    }
  },

  async rebuildProfile(req, res) {
    try {
      const days = Number((req.body || {}).days) || 30;
      res.json({ success: true, profile: userProfileService.rebuild(Math.max(1, Math.min(365, days))) });
    } catch (e) {
      res.status(500).json({ error: e?.message || "重建失败" });
    }
  },

  async getStrategies(req, res) {
    try {
      res.json({ success: true, strategies: strategyLibrary.list(Number(req.query.limit) || 50) });
    } catch (e) {
      res.status(500).json({ error: e?.message || "读取失败" });
    }
  },

  async deleteStrategy(req, res) {
    try {
      res.json({ success: strategyLibrary.remove(req.params.id) });
    } catch (e) {
      res.status(500).json({ error: e?.message || "删除失败" });
    }
  },

  async evaluateStrategies(req, res) {
    try {
      const { minReuseCount = 10, minSuccessRate = 0.3, minAvgScore = 0.5 } = req.body || {};
      const result = strategyLibrary.evaluateAndPrune(
        Number(minReuseCount),
        Number(minSuccessRate),
        Number(minAvgScore)
      );
      res.json({ success: true, ...result });
    } catch (e) {
      res.status(500).json({ error: e?.message || "评估失败" });
    }
  },

  async getAutonomousTasks(req, res) {
    try {
      const sessionId = req.query.sessionId ? String(req.query.sessionId) : null;
      const status = req.query.status ? String(req.query.status) : null;
      const conditions = [];
      const params = [];
      if (sessionId) { conditions.push("session_id = ?"); params.push(sessionId); }
      if (status) { conditions.push("status = ?"); params.push(status); }
      const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";
      const rows = db.prepare(`SELECT * FROM autonomous_tasks ${where} ORDER BY updated_at DESC LIMIT 30`).all(...params);
      res.json({ success: true, tasks: rows.map(mapAutonomousTask) });
    } catch (e) {
      res.status(500).json({ error: e?.message || "读取失败" });
    }
  },

  async queueTask(req, res) {
    try {
      const {
        sessionId, goal, priority = 0, dueAt = null, goalId = null, model = null,
        allowWrites = true, recurrence = "none", recurTime = "09:00",
        recurWeekday = 1, recurMonthday = 1, maxRetry = 2, timeoutSec = 900
      } = req.body || {};
      if (!goal) return res.status(400).json({ error: "任务目标必填" });
      if (!sessionId) return res.status(400).json({ error: "会话 ID 必填" });
      const recur = ["none", "daily", "weekly", "monthly"].includes(recurrence) ? recurrence : "none";
      if (!/^\d{1,2}:\d{2}$/.test(String(recurTime || ""))) {
        return res.status(400).json({ error: "循环时间格式应为 HH:MM" });
      }
      const paddedTime = String(recurTime).padStart(5, "0");
      const weekday = Math.min(Math.max(Number(recurWeekday) || 0, 0), 6);
      const monthday = Math.min(Math.max(Number(recurMonthday) || 1, 1), 31);
      const nextDue = dueAt || (recur !== "none"
        ? computeNextRun({ recurrence: recur, recur_time: paddedTime, recur_weekday: weekday, recur_monthday: monthday }, new Date())
        : null);
      const task = taskQueue.enqueue({
        sessionId, goal,
        priority: Number(priority) || 0,
        dueAt: nextDue,
        goalId: goalId || null,
        model: model || null,
        allowWrites: allowWrites === false ? 0 : 1,
        recurrence: recur,
        recurTime: paddedTime,
        recurWeekday: weekday,
        recurMonthday: monthday,
        maxRetry: Math.min(Math.max(Number(maxRetry) || 0, 0), 5),
        timeoutSec: Math.min(Math.max(Number(timeoutSec) || 900, 60), 7200)
      });
      res.json({ success: true, task: mapAutonomousTask(task) });
    } catch (e) {
      res.status(500).json({ error: e?.message || "入队失败" });
    }
  },

  async retryTask(req, res) {
    try {
      const ok = taskQueue.requeue(req.params.id);
      setTimeout(() => taskQueue.tick().catch(() => {}), 20).unref?.();
      res.json({ success: ok });
    } catch (e) {
      res.status(500).json({ error: e?.message || "重试失败" });
    }
  },

  async abandonTask(req, res) {
    try {
      const r = db.prepare("UPDATE autonomous_tasks SET status = 'abandoned', updated_at = ? WHERE id = ?")
        .run(new Date().toISOString(), req.params.id);
      res.json({ success: r.changes > 0 });
    } catch (e) {
      res.status(500).json({ error: e?.message || "操作失败" });
    }
  },

  async getGoals(req, res) {
    try {
      const status = req.query.status ? String(req.query.status) : null;
      res.json({ success: true, goals: goalService.list(status) });
    } catch (e) {
      res.status(500).json({ error: e?.message || "读取失败" });
    }
  },

  async createGoal(req, res) {
    try {
      const { title, description, acceptance, deadline, sessionId, priority } = req.body || {};
      res.json({ success: true, goal: goalService.create({ title, description, acceptance, deadline, sessionId, priority }) });
    } catch (e) {
      res.status(400).json({ error: e?.message || "创建失败" });
    }
  },

  async updateGoal(req, res) {
    try {
      res.json({ success: true, goal: goalService.update(req.params.id, req.body || {}) });
    } catch (e) {
      res.status(500).json({ error: e?.message || "更新失败" });
    }
  },

  async deleteGoal(req, res) {
    try {
      res.json({ success: goalService.remove(req.params.id) });
    } catch (e) {
      res.status(500).json({ error: e?.message || "删除失败" });
    }
  },

  async reviewGoal(req, res) {
    try {
      const result = await goalService.review(req.params.id);
      res.json({ success: true, goal: result });
    } catch (e) {
      res.status(500).json({ error: e?.message || "验收失败" });
    }
  },

  async getPendingProactive(req, res) {
    try {
      const sessionId = req.query.sessionId ? String(req.query.sessionId) : null;
      if (sessionId) {
        const rows = db.prepare(
          "SELECT id, session_id, service_type, trigger_condition, action, status, priority, weight, created_at FROM proactive_services WHERE session_id = ? AND status = 'pending' ORDER BY priority DESC, weight DESC, created_at DESC LIMIT 10"
        ).all(sessionId);
        return res.json({ success: true, items: rows });
      }
      const items = db.prepare(
        "SELECT id, session_id, service_type, trigger_condition, action, status, priority, weight, created_at FROM proactive_services WHERE status = 'pending' ORDER BY priority DESC, weight DESC, created_at DESC LIMIT 20"
      ).all();
      res.json({ success: true, items });
    } catch (error) {
      res.status(500).json({ error: error?.message || "获取失败" });
    }
  },

  async resolveProactive(req, res) {
    try {
      const feedback = String((req.body || {}).feedback || "accepted") === "dismissed" ? "dismissed" : "accepted";
      const status = feedback === "dismissed" ? "dismissed" : "executed";
      const now = nowIso();
      const r = db.prepare(
        "UPDATE proactive_services SET status = ?, feedback = ?, feedback_at = ?, executed_at = ? WHERE id = ?"
      ).run(status, feedback, now, feedback === "executed" ? now : null, req.params.id);
      res.json({ success: r.changes > 0, feedback });
    } catch (error) {
      res.status(500).json({ error: error?.message || "更新失败" });
    }
  }
};

// 从 server.mjs 导入的函数
// computeNextRun, mapAutonomousTask, userProfileService, proactivePatrol, taskQueue, goalService, strategyLibrary
// nowIso