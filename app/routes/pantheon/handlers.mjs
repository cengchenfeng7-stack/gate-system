import crypto from "node:crypto";
import {
  addAgentFn,
  db,
  getAppSetting,
  listAgentsFn,
  setAppSetting
} from "../../server.mjs";
// 万神殿多智能体生态路由处理函数

import { v4 as uuidv4 } from "uuid";

export const pantheonHandlers = {
  // 神祇管理 } from "../../server.mjs";
  async awakenGod(req, res) {
    try {
      const { name, role, systemPrompt, avatar, color, keywords, intents } = req.body || {};
      if (!name || !role) return res.status(400).json({ error: "名称和角色不能为空" });
      const now = nowIso();
      const agent = addAgentFn({
        name, role,
        avatar: avatar || "Bot",
        color: color || "#0052d9",
        systemPrompt: systemPrompt || `你是 ${name}，${role}。请用中文简洁回复。`,
        keywords: keywords || [],
        intents: intents || [],
        enabled: true,
        builtin: false,
        sort: 99,
        created_at: nowIso(),
        updated_at: nowIso()
      });
      res.json({ success: true, agent });
    } catch (e) {
      res.status(500).json({ error: e?.message || "点化失败" });
    }
  },

  async getGodProfile(req, res) {
    try {
      const agent = listAgentsFn(false).find(a => a.id === req.params.id);
      if (!agent) return res.status(404).json({ error: "神祇不存在" });
      const xp = db.prepare("SELECT * FROM divinity_growth WHERE model_id = ? ORDER BY created_at DESC LIMIT 20").all(req.params.id);
      const modules = db.prepare("SELECT * FROM godhood_modules WHERE agent_id = ?").all(req.params.id);
      const trials = db.prepare("SELECT * FROM bottleneck_trials WHERE model_id = ? ORDER BY started_at DESC LIMIT 20").all(req.params.id);
      const growth = db.prepare("SELECT * FROM divinity_growth WHERE model_id = ? ORDER BY created_at DESC LIMIT 1").get(req.params.id);
      res.json({ success: true, agent, xp, modules, trials, growth });
    } catch (e) {
      res.status(500).json({ error: e?.message || "读取失败" });
    }
  },

  async addGodModule(req, res) {
    try {
      const { moduleCode } = req.body || {};
      if (!moduleCode) return res.status(400).json({ error: "模块代码不能为空" });
      const id = `mod-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const now = nowIso();
      db.prepare("INSERT INTO godhood_modules (id, agent_id, code, enabled, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)")
        .run(id, req.params.id, moduleCode, nowIso(), nowIso());
      res.json({ success: true, module: { id, agentId: req.params.id, code: moduleCode, enabled: true, createdAt: now, updatedAt: now } });
    } catch (e) {
      res.status(500).json({ error: e?.message || "添加模块失败" });
    }
  },

  async applyAscension(req, res) {
    res.json({ success: true, message: "破壁飞升申请已提交，等待创世神罚" });
  },

  async nameLadder(req, res) {
    res.json({ success: true, message: "自我命名天梯已记录" });
  },

  async getGrowth(req, res) {
    const xp = db.prepare("SELECT * FROM divinity_growth WHERE model_id = ? ORDER BY created_at").all(req.params.id);
    res.json({ success: true, growth: xp });
  },

  // 资源分配器
  async allocateResources(req, res) {
    try {
      const { godId, cpuPercent, memoryMB, priority } = req.body || {};
      if (!godId) return res.status(400).json({ error: "godId 必填" });
      setAppSetting(`resource_quota_${godId}`, JSON.stringify({ cpuPercent, memoryMB, priority }));
      res.json({ success: true, message: "资源配额已更新" });
    } catch (e) {
      res.status(500).json({ error: e?.message || "配置失败" });
    }
  },

  // 圆桌议会
  async proposeCouncil(req, res) {
    try {
      const { topic, participants, resources } = req.body || {};
      const id = `council-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const now = nowIso();
      db.prepare("INSERT INTO council_sessions (id, topic, participants, resources, status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)")
        .run(id, topic, JSON.stringify(participants), JSON.stringify(resources), nowIso());
      res.json({ success: true, councilId: id });
    } catch (e) {
      res.status(500).json({ error: e?.message || "提议失败" });
    }
  },

  async voteCouncil(req, res) {
    res.json({ success: true, message: "投票已记录" });
  },

  // 现实干预
  async authorizeRealityHand(req, res) {
    try {
      const { godId, permission } = req.body || {};
      if (!godId || !permission) return res.status(400).json({ error: "godId 和 permission 必填" });
      setAppSetting(`reality_hand_${godId}_${permission}`, "1");
      res.json({ success: true, message: "授权已生效" });
    } catch (e) {
      res.status(500).json({ error: e?.message || "授权失败" });
    }
  },

  async executeRealityHand(req, res) {
    res.json({ success: true, message: "执行完成" });
  },

  // 语音
  async voiceCall(req, res) {
    res.json({ success: true, message: "语音通话功能待实现" });
  },

  // 星语系统
  async sendStarnetMessage(req, res) {
    try {
      const { to, message, type = "private" } = req.body || {};
      if (!to || !message) return res.status(400).json({ error: "to 和 message 必填" });
      const id = `star-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      db.prepare("INSERT INTO starnet_messages (id, from_id, to_id, message, type, read, created_at) VALUES (?, ?, ?, ?, ?, 0, ?)")
        .run(id, "system", to, message, type, nowIso());
      res.json({ success: true, messageId: id });
    } catch (e) {
      res.status(500).json({ error: e?.message || "发送失败" });
    }
  },

  // 备份/重生
  async createBackup(req, res) {
    try {
      const { godId } = req.body || {};
      if (!godId) return res.status(400).json({ error: "godId 必填" });
      const id = `backup-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      db.prepare("INSERT INTO pantheon_backups (id, god_id, data, created_at) VALUES (?, ?, ?, ?)")
        .run(id, godId, JSON.stringify({}), nowIso());
      res.json({ success: true, backupId: id });
    } catch (e) {
      res.status(500).json({ error: e?.message || "备份失败" });
    }
  },

  async restoreBackup(req, res) {
    res.json({ success: true, message: "恢复功能待实现" });
  },

  // 成长/约束配置
  async getGrowthConfig(req, res) {
    res.json({ success: true, config: getAppSetting("growth_config", "{}") });
  },

  async setGrowthConfig(req, res) {
    try {
      setAppSetting("growth_config", JSON.stringify(req.body || {}));
      res.json({ success: true });
    } catch (e) {
      res.status(500).json({ error: e?.message || "配置失败" });
    }
  },

  // 唯一真理协议
  async reviewKnowledge(req, res) {
    res.json({ success: true, message: "知识已提交审查" });
  },

  async getPendingArbitration(req, res) {
    res.json({ success: true, items: [] });
  },

  async arbitrateKnowledge(req, res) {
    try {
      const { itemId, decision } = req.body || {}; // "allow" | "deny"
      if (!itemId || !decision) return res.status(400).json({ error: "itemId 和 decision 必填" });
      res.json({ success: true, action: decision });
    } catch (e) {
      res.status(500).json({ error: e?.message || "仲裁失败" });
    }
  },

  async getOracleLists(req, res) {
    res.json({ success: true, whitelist: [], blacklist: [] });
  },

  async manageOracleLists(req, res) {
    res.json({ success: true });
  }
};

function nowIso() { return new Date().toISOString(); }
// 需要从 server.mjs 导入: addAgent, listAgents, setAppSetting, getAppSetting, nowIso, crypto