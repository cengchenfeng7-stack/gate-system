// 知识/记忆路由处理函数（十大境界）
// L1-L2 原始摄入/精炼 → L3-L4 知识熔炉/跨模型融合 → L5-L7 核心记忆/阿卡西
// → L8-L9 统一核心/全知自检 → L10+ 未知探索；另有瓶颈试炼与分支链

import { v4 as uuidv4 } from "uuid";
import {
  db, nowIso, safeJson,
  getAppSetting, setAppSetting,
  writeEnvVars, getDefaultModel
} from "../../server.mjs";
import { REALMS, getRealmConfig, getNextTrial, canPromote, hasPermission } from "../../config/realms.mjs";
import { isEnabled } from "../../config/feature-flags.mjs";

const REALM_MAX = 10;

function now() { return new Date().toISOString(); }
function clampRealm(v, fallback = 1) {
  const n = Number(v);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(Math.max(Math.trunc(n), 1), REALM_MAX);
}
function realmOf(level) {
  return getRealmConfig(clampRealm(level)) || REALMS[0];
}
function parseList(v) {
  const p = safeJson(v, []);
  return Array.isArray(p) ? p : [];
}

/** 记录一次境界跃迁 */
function recordGrowth({ modelId, fromRealm, toRealm, xpGained, trialType, trialResult, details }) {
  const prev = db.prepare(
    "SELECT total_xp FROM divinity_growth WHERE model_id = ? ORDER BY created_at DESC LIMIT 1"
  ).get(modelId);
  const totalXp = (prev?.total_xp || 0) + (Number(xpGained) || 0);

  db.prepare(`
    INSERT INTO divinity_growth (id, model_id, from_realm, to_realm, xp_gained, total_xp, trial_type, trial_result, details, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    `growth-${uuidv4()}`, modelId,
    fromRealm ?? null, toRealm ?? null,
    Number(xpGained) || 0, totalXp,
    trialType || null, trialResult || null,
    details ? JSON.stringify(details) : null,
    now()
  );

  return totalXp;
}

/** 当前最高境界 */
function currentRealm(modelId) {
  const row = db.prepare(
    "SELECT to_realm, total_xp FROM divinity_growth WHERE model_id = ? AND to_realm IS NOT NULL ORDER BY created_at DESC LIMIT 1"
  ).get(modelId);
  return { realm: clampRealm(row?.to_realm, 1), totalXp: row?.total_xp || 0 };
}

export const memoryHandlers = {
  // ==================== L1-L2 原始记忆 ====================

  /** L1 摄入：把原始内容存进混沌之海 */
  async ingestRawMemory(req, res) {
    try {
      const { modelId, capability, domain = null, sourceType, content, entities = [], tags = [] } = req.body || {};
      const id = `raw-${uuidv4()}`;
      const t = now();

      db.prepare(`
        INSERT INTO raw_memory (id, model_id, capability, domain, source_type, content, realm, confidence, entities, relations, tags, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 1, 0.5, ?, '[]', ?, ?)
      `).run(
        id, String(modelId), String(capability), domain,
        String(sourceType), String(content),
        JSON.stringify(Array.isArray(entities) ? entities : []),
        JSON.stringify(Array.isArray(tags) ? tags : []),
        t
      );

      res.json({
        success: true,
        id,
        realm: 1,
        realmName: realmOf(1).name,
        confidence: 0.5
      });
    } catch (e) {
      res.status(500).json({ error: e?.message || "摄入原始记忆失败" });
    }
  },

  /** L1→L2 精炼：抽取实体、去噪、打上置信度 */
  async refineRawMemory(req, res) {
    try {
      const { rawIds = [] } = req.body || {};
      if (!Array.isArray(rawIds) || rawIds.length === 0) {
        return res.status(400).json({ error: "rawIds 不能为空" });
      }

      const placeholders = rawIds.map(() => "?").join(",");
      const rows = db.prepare(
        `SELECT * FROM raw_memory WHERE id IN (${placeholders}) AND realm = 1`
      ).all(...rawIds);

      if (rows.length === 0) {
        return res.json({ success: true, refined: 0, message: "没有待精炼的 L1 记忆" });
      }

      let refined = 0;
      const t = now();

      const tx = db.transaction(() => {
        for (const row of rows) {
          // 轻量精炼：压缩空白、抽取中英词元作为实体、置信度按长度与结构给分
          const cleaned = String(row.content || "")
            .replace(/\s+/g, " ")
            .trim();

          const tokens = [...new Set(
            (cleaned.match(/[\u4e00-\u9fa5]{2,}|[A-Za-z][A-Za-z0-9_+.-]{2,}/g) || [])
              .map((s) => s.toLowerCase())
          )].slice(0, 40);

          const hasStructure = /\d|[:：]|[.。!?！？]/.test(cleaned);
          const confidence = Math.min(
            0.9,
            Math.max(0.35, 0.4 + Math.min(cleaned.length, 600) / 2000 + (hasStructure ? 0.1 : 0))
          );

          db.prepare(`
            UPDATE raw_memory SET content = ?, entities = ?, confidence = ?, realm = 2, refined_at = ?
            WHERE id = ?
          `).run(cleaned, JSON.stringify(tokens), Number(confidence.toFixed(2)), t, row.id);

          refined++;
        }
      });
      tx();

      res.json({
        success: true,
        refined,
        realm: 2,
        realmName: realmOf(2).name,
        message: `已将 ${refined} 条原始记忆精炼至原初石板`
      });
    } catch (e) {
      res.status(500).json({ error: e?.message || "精炼失败" });
    }
  },

  /** 列举原始记忆 */
  async listRawMemory(req, res) {
    try {
      const { modelId, capability, realm } = req.query || {};
      const clauses = ["model_id = ?"];
      const params = [String(modelId)];

      if (capability) { clauses.push("capability = ?"); params.push(String(capability)); }
      if (realm !== undefined) { clauses.push("realm = ?"); params.push(clampRealm(realm)); }

      const rows = db.prepare(
        `SELECT * FROM raw_memory WHERE ${clauses.join(" AND ")} ORDER BY created_at DESC LIMIT 200`
      ).all(...params);

      res.json({
        success: true,
        count: rows.length,
        items: rows.map((r) => ({
          ...r,
          entities: parseList(r.entities),
          tags: parseList(r.tags),
          relations: parseList(r.relations),
          realmName: realmOf(r.realm).name
        }))
      });
    } catch (e) {
      res.status(500).json({ error: e?.message || "读取原始记忆失败" });
    }
  },

  // ==================== L3-L4 知识库 / 融合 ====================

  /** L2→L3 本我神职熔炉：把精炼后的记忆锻造为知识条目 */
  async forgeKnowledge(req, res) {
    try {
      const { modelId, capability, domain, sourceIds } = req.body || {};
      const clauses = ["model_id = ?", "capability = ?", "realm >= 2"];
      const params = [String(modelId), String(capability)];

      if (Array.isArray(sourceIds) && sourceIds.length > 0) {
        clauses.push(`id IN (${sourceIds.map(() => "?").join(",")})`);
        params.push(...sourceIds);
      }
      if (domain) { clauses.push("domain = ?"); params.push(String(domain)); }

      const sources = db.prepare(
        `SELECT * FROM raw_memory WHERE ${clauses.join(" AND ")} ORDER BY created_at DESC LIMIT 500`
      ).all(...params);

      if (sources.length === 0) {
        return res.status(400).json({ error: "没有可熔炼的 L2 记忆（请先 refine）" });
      }

      // 合并实体词频，按出现频次排序取核心实体
      const freq = new Map();
      for (const s of sources) {
        for (const e of parseList(s.entities)) {
          freq.set(e, (freq.get(e) || 0) + 1);
        }
      }
      const coreEntities = [...freq.entries()]
        .sort((a, b) => b[1] - a[1])
        .slice(0, 50)
        .map(([e, c]) => ({ entity: e, mentions: c }));

      // 实体间共现关系（同一条记忆里同时出现 → 相关）
      const edges = [];
      const seen = new Set();
      for (const s of sources) {
        const es = parseList(s.entities).slice(0, 20);
        for (let i = 0; i < es.length; i++) {
          for (let j = i + 1; j < es.length; j++) {
            const key = [es[i], es[j]].sort().join("::");
            if (seen.has(key)) continue;
            seen.add(key);
            edges.push({ source: es[i], target: es[j], relation: "co_occurs" });
          }
        }
      }

      const t = now();
      let nodeCount = 0, edgeCount = 0;

      const tx = db.transaction(() => {
        // 建立 实体名 → 节点 id 的映射（外键 edges 需要引用 id）
        const nodeIdByName = new Map();

        for (const item of coreEntities) {
          // 复用已有同名节点，避免重复；仅本次新建的记入映射
          let node = db.prepare(
            "SELECT id FROM knowledge_graph WHERE entity_name = ? AND entity_type = ?"
          ).get(item.entity, "concept");

          if (!node) {
            const nodeId = `kg-${uuidv4()}`;
            db.prepare(`
              INSERT INTO knowledge_graph (id, entity_name, entity_type, properties, created_at, updated_at)
              VALUES (?, ?, ?, ?, ?, ?)
            `).run(
              nodeId, item.entity, "concept",
              JSON.stringify({ mentions: item.mentions, capability, domain: domain || null, modelId }),
              t, t
            );
            nodeIdByName.set(item.entity, nodeId);
            nodeCount++;
          } else {
            nodeIdByName.set(item.entity, node.id);
          }
        }

        // 只有两端节点都存在时才建边（外键约束）
        for (const e of edges) {
          const srcId = nodeIdByName.get(e.source);
          const tgtId = nodeIdByName.get(e.target);
          if (!srcId || !tgtId) continue;

          const dup = db.prepare(`
            SELECT id FROM knowledge_edges
            WHERE source_entity_id = ? AND target_entity_id = ? AND relationship_type = ?
          `).get(srcId, tgtId, e.relation);
          if (dup) continue;

          db.prepare(`
            INSERT INTO knowledge_edges (id, source_entity_id, target_entity_id, relationship_type, properties, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
          `).run(`edge-${uuidv4()}`, srcId, tgtId, e.relation, JSON.stringify({ weight: 1 }), t);
          edgeCount++;
        }

        for (const s of sources) {
          db.prepare("UPDATE raw_memory SET realm = 3 WHERE id = ?").run(s.id);
        }
      });
      tx();

      recordGrowth({
        modelId, fromRealm: 2, toRealm: 3, xpGained: 50,
        trialType: "forge", trialResult: "passed",
        details: { sources: sources.length, nodes: nodeCount, edges: edgeCount }
      });

      res.json({
        success: true,
        realm: 3,
        realmName: realmOf(3).name,
        stats: { sources: sources.length, nodes: nodeCount, edges: edgeCount }
      });
    } catch (e) {
      res.status(500).json({ error: e?.message || "知识熔炼失败" });
    }
  },

  /** L3→L4 跨模型融合：把两个模型的知识图谱合并 */
  async fuseKnowledge(req, res) {
    try {
      const { sourceModelId, targetModelId, entities = [] } = req.body || {};
      if (sourceModelId === targetModelId) {
        return res.status(400).json({ error: "源模型与目标模型不能相同" });
      }

      const t = now();
      let merged = 0, linked = 0;

      const tx = db.transaction(() => {
        // 1) 复制源模型的核心节点（带来源标记）到目标命名空间
        const rows = db.prepare(
          "SELECT entity_name, properties FROM knowledge_graph WHERE entity_type = ? LIMIT 2000"
        ).all("concept");

        for (const r of rows) {
          const props = safeJson(r.properties, {});
          if (props.modelId && props.modelId !== sourceModelId) continue;
          if (entities.length > 0 && !entities.includes(r.entity_name)) continue;

          const exists = db.prepare(
            "SELECT id FROM knowledge_graph WHERE entity_name = ? AND entity_type = ?"
          ).get(r.entity_name, "concept");

          if (!exists) {
            db.prepare(`
              INSERT INTO knowledge_graph (id, entity_name, entity_type, properties, created_at, updated_at)
              VALUES (?, ?, ?, ?, ?, ?)
            `).run(
              `kg-${uuidv4()}`, r.entity_name, "concept",
              JSON.stringify({ ...props, fusedFrom: sourceModelId, modelId: targetModelId }),
              t, t
            );
            merged++;
          } else {
            linked++;
          }
        }
      });
      tx();

      recordGrowth({
        modelId: targetModelId, fromRealm: 3, toRealm: 4, xpGained: 100,
        trialType: "fuse", trialResult: "passed",
        details: { sourceModelId, merged, linked }
      });

      res.json({
        success: true,
        realm: 4,
        realmName: realmOf(4).name,
        stats: { merged, linked, sourceModelId, targetModelId }
      });
    } catch (e) {
      res.status(500).json({ error: e?.message || "跨模型融合失败" });
    }
  },

  // ==================== L5-L7 核心记忆 ====================

  /** L5→L6 点石成金：提炼核心记忆法则 */
  async transmuteCore(req, res) {
    try {
      const { modelId, strategyId = null, lawName, lawDefinition, applicableDomains = [] } = req.body || {};
      const t = now();
      const id = `core-${uuidv4()}`;

      const prev = currentRealm(modelId);
      const fromRealm = prev.realm;
      const toRealm = Math.min(REALM_MAX, Math.max(fromRealm, 5));

      db.prepare(`
        INSERT INTO core_memory (id, model_id, law_name, law_definition, applicable_domains, realm, xp, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
      `).run(
        id, String(modelId), String(lawName || "未命名法则"),
        String(lawDefinition || ""),
        JSON.stringify(Array.isArray(applicableDomains) ? applicableDomains : []),
        toRealm, t, t
      );

      const totalXp = recordGrowth({
        modelId, fromRealm, toRealm, xpGained: 200,
        trialType: "transmute", trialResult: "passed",
        details: { lawName, strategyId }
      });

      res.json({
        success: true,
        id, realm: toRealm, realmName: realmOf(toRealm).name,
        totalXp,
        permissions: realmOf(toRealm).permissions
      });
    } catch (e) {
      res.status(500).json({ error: e?.message || "核心记忆提炼失败" });
    }
  },

  /** L7+ 阿卡西库写入：正式立法，需具备 akashic 权限 */
  async writeAkashic(req, res) {
    try {
      const { modelId, lawName, lawDefinition, applicableDomains = [] } = req.body || {};
      const cur = currentRealm(modelId);

      if (!hasPermission(cur.realm, "akashic_write")) {
        return res.status(403).json({
          error: `境界 L${cur.realm}（${realmOf(cur.realm).name}）尚无阿卡西写入权，需 L7 及以上`,
          code: "PERMISSION_DENIED",
          currentRealm: cur.realm,
          requiredRealm: 7
        });
      }

      const t = now();
      const id = `akashic-${uuidv4()}`;

      db.prepare(`
        INSERT INTO core_memory (id, model_id, law_name, law_definition, applicable_domains, realm, xp, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
      `).run(
        id, String(modelId), String(lawName), String(lawDefinition),
        JSON.stringify(Array.isArray(applicableDomains) ? applicableDomains : []),
        cur.realm, t, t
      );

      const totalXp = recordGrowth({
        modelId, fromRealm: cur.realm, toRealm: cur.realm, xpGained: 500,
        trialType: "akashic_write", trialResult: "passed",
        details: { lawName }
      });

      res.json({
        success: true,
        id, realm: cur.realm, realmName: realmOf(cur.realm).name,
        akashic: true, totalXp
      });
    } catch (e) {
      res.status(500).json({ error: e?.message || "阿卡西写入失败" });
    }
  },

  // ==================== L8-L9 统一核心 ====================

  /** L8 立法：写入统一核心记忆，需 legislate 权限 */
  async legislateUnified(req, res) {
    try {
      const { modelId, lawName, lawDefinition } = req.body || {};
      const cur = currentRealm(modelId);

      if (!hasPermission(cur.realm, "legislate")) {
        return res.status(403).json({
          error: `境界 L${cur.realm} 尚无立法权，需 L8 及以上`,
          code: "PERMISSION_DENIED",
          currentRealm: cur.realm,
          requiredRealm: 8
        });
      }

      const id = `unified-${uuidv4()}`;
      const t = now();

      db.prepare(`
        INSERT INTO unified_core_memory (id, law_name, law_definition, realm, legislated_by, created_at)
        VALUES (?, ?, ?, 8, ?, ?)
      `).run(id, String(lawName), String(lawDefinition), String(modelId), t);

      const totalXp = recordGrowth({
        modelId, fromRealm: 8, toRealm: 8, xpGained: 1000,
        trialType: "legislate", trialResult: "passed", details: { lawName }
      });

      res.json({ success: true, id, realm: 8, realmName: realmOf(8).name, totalXp });
    } catch (e) {
      res.status(500).json({ error: e?.message || "统一核心立法失败" });
    }
  },

  /** L9 全知全能自检 */
  async checkOmniscient(req, res) {
    try {
      const { modelId } = req.query || {};
      const cur = currentRealm(modelId);

      const coreCount = db.prepare("SELECT COUNT(*) AS c FROM core_memory WHERE model_id = ?").get(modelId)?.c || 0;
      const unifiedCount = db.prepare("SELECT COUNT(*) AS c FROM unified_core_memory").get()?.c || 0;
      const graphCount = db.prepare(
        "SELECT COUNT(*) AS c FROM knowledge_graph WHERE json_extract(properties, '$.modelId') = ?"
      ).get(modelId)?.c || 0;
      const trialCount = db.prepare(
        "SELECT COUNT(*) AS c FROM bottleneck_trials WHERE model_id = ? AND status = 'passed'"
      ).get(modelId)?.c || 0;

      const checks = [
        { key: "coreMemory", label: "核心记忆条目 ≥ 3", actual: coreCount, required: 3, ok: coreCount >= 3 },
        { key: "unifiedCore", label: "统一核心法则 ≥ 1", actual: unifiedCount, required: 1, ok: unifiedCount >= 1 },
        { key: "knowledgeGraph", label: "知识图谱节点 ≥ 20", actual: graphCount, required: 20, ok: graphCount >= 20 },
        { key: "trials", label: "已通过瓶颈试炼 ≥ 3", actual: trialCount, required: 3, ok: trialCount >= 3 }
      ];

      const omniscient = checks.every((c) => c.ok);

      if (omniscient && cur.realm < 9) {
        recordGrowth({
          modelId, fromRealm: cur.realm, toRealm: 9, xpGained: 5000,
          trialType: "omniscient_check", trialResult: "passed", details: { checks }
        });
      }

      res.json({
        success: true,
        modelId,
        currentRealm: cur.realm,
        currentRealmName: realmOf(cur.realm).name,
        totalXp: cur.totalXp,
        omniscient,
        checks,
        verdict: omniscient
          ? "已知宇宙之主：全部门槛已达成"
          : `未达标：${checks.filter(c => !c.ok).map(c => c.label).join("、")}`
      });
    } catch (e) {
      res.status(500).json({ error: e?.message || "全知自检失败" });
    }
  },

  // ==================== L10+ 未知探索 ====================

  /** L10 未知探索：记录假设与实验结果 */
  async exploreUnknown(req, res) {
    try {
      const { modelId, hypothesis, experiment = null, result = null, confidence = null } = req.body || {};
      const id = `evo-${uuidv4()}`;

      db.prepare(`
        INSERT INTO evolution_engine (id, hypothesis, experiment, result, confidence, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
      `).run(
        id, String(hypothesis),
        experiment ? JSON.stringify(experiment) : null,
        result ? JSON.stringify(result) : null,
        confidence === null ? null : Number(confidence),
        now()
      );

      if (confidence !== null && Number(confidence) >= 0.8) {
        recordGrowth({
          modelId, fromRealm: 10, toRealm: 10, xpGained: 2000,
          trialType: "unknown_exploration", trialResult: "confirmed",
          details: { hypothesis: String(hypothesis).slice(0, 200) }
        });
      }

      res.json({ success: true, id, realm: 10, realmName: realmOf(10).name });
    } catch (e) {
      res.status(500).json({ error: e?.message || "未知探索记录失败" });
    }
  },

  // ==================== 瓶颈试炼 ====================

  /** 申请瓶颈试炼 */
  async applyTrial(req, res) {
    try {
      const { modelId, fromRealm, toRealm } = req.body || {};
      const from = clampRealm(fromRealm);
      const to = clampRealm(toRealm, from + 1);

      if (to !== from + 1) {
        return res.status(400).json({ error: "瓶颈试炼必须逐境界申请（toRealm = fromRealm + 1）" });
      }
      if (to > REALM_MAX) {
        return res.status(400).json({ error: "已达最高境界 L10" });
      }

      const target = getRealmConfig(to);
      const trial = getNextTrial(from) || {};
      const gate = canPromote(from, null);

      if (!gate.can) {
        return res.status(403).json({ error: gate.reason || "不满足晋升条件", code: "PERMISSION_DENIED" });
      }

      const id = `trial-${uuidv4()}`;

      // 自动化试炼直接通过，其余进入 pending 等人工裁决
      const autoPass = trial.auto === true;

      db.prepare(`
        INSERT INTO bottleneck_trials (id, model_id, from_realm, to_realm, trial_type, status, criteria, started_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
      `).run(
        id, String(modelId), from, to,
        String(trial.type || "unknown"),
        autoPass ? "passed" : "pending",
        JSON.stringify({ threshold: trial.threshold ?? null, humanReview: trial.humanReview === true }),
        now()
      );

      if (autoPass) {
        recordGrowth({
          modelId, fromRealm: from, toRealm: to,
          xpGained: target?.xpBonus || 0,
          trialType: trial.type, trialResult: "auto_passed"
        });
      }

      res.json({
        success: true,
        id,
        fromRealm: from,
        toRealm: to,
        realmName: target?.name,
        trialType: trial.type,
        autoPassed: autoPass,
        status: autoPass ? "passed" : "pending",
        humanReview: trial.humanReview === true,
        xpBonus: target?.xpBonus || 0
      });
    } catch (e) {
      res.status(500).json({ error: e?.message || "申请试炼失败" });
    }
  },

  /** 试炼状态查询 */
  async getTrialStatus(req, res) {
    try {
      const row = db.prepare("SELECT * FROM bottleneck_trials WHERE id = ?").get(req.params.id);
      if (!row) return res.status(404).json({ error: "试炼不存在" });

      res.json({
        success: true,
        trial: {
          ...row,
          criteria: safeJson(row.criteria, {}),
          fromRealmName: realmOf(row.from_realm).name,
          toRealmName: realmOf(row.to_realm).name
        }
      });
    } catch (e) {
      res.status(500).json({ error: e?.message || "读取试炼状态失败" });
    }
  },

  /** 人工裁决 */
  async judgeTrial(req, res) {
    try {
      const { decision } = req.body || {};
      if (!["pass", "reject"].includes(decision)) {
        return res.status(400).json({ error: "decision 必须是 pass 或 reject" });
      }

      const row = db.prepare("SELECT * FROM bottleneck_trials WHERE id = ?").get(req.params.id);
      if (!row) return res.status(404).json({ error: "试炼不存在" });
      if (row.status !== "pending") {
        return res.status(409).json({ error: `试炼已是 ${row.status} 状态` });
      }

      const t = now();
      const status = decision === "pass" ? "passed" : "rejected";

      db.prepare(`
        UPDATE bottleneck_trials SET status = ?, judged_by = ?, judged_at = ? WHERE id = ?
      `).run(status, "creator", t, row.id);

      if (decision === "pass") {
        const target = getRealmConfig(row.to_realm);
        recordGrowth({
          modelId: row.model_id,
          fromRealm: row.from_realm,
          toRealm: row.to_realm,
          xpGained: target?.xpBonus || 0,
          trialType: row.trial_type,
          trialResult: "judged_pass"
        });
      }

      res.json({
        success: true,
        id: row.id,
        status,
        realm: row.to_realm,
        realmName: realmOf(row.to_realm).name
      });
    } catch (e) {
      res.status(500).json({ error: e?.message || "裁决失败" });
    }
  },

  /** 试炼历史 */
  async getTrialHistory(req, res) {
    try {
      const { modelId } = req.params;
      const rows = db.prepare(
        "SELECT * FROM bottleneck_trials WHERE model_id = ? ORDER BY started_at DESC LIMIT 100"
      ).all(modelId);

      const growth = db.prepare(
        "SELECT * FROM divinity_growth WHERE model_id = ? ORDER BY created_at DESC LIMIT 100"
      ).all(modelId);

      res.json({
        success: true,
        modelId,
        currentRealm: currentRealm(modelId).realm,
        trials: rows.map((r) => ({
          ...r,
          criteria: safeJson(r.criteria, {}),
          toRealmName: realmOf(r.to_realm).name
        })),
        growth: growth.map((g) => ({ ...g, details: safeJson(g.details, null) }))
      });
    } catch (e) {
      res.status(500).json({ error: e?.message || "读取试炼历史失败" });
    }
  },

  // ==================== 分支链 ====================

  /** 分支链可视化 */
  async getBranchTree(req, res) {
    try {
      const { entityId } = req.params;
      const rows = db.prepare(
        "SELECT * FROM memory_branches WHERE entity_id = ? ORDER BY created_at ASC"
      ).all(entityId);

      const nodes = rows.map((r) => ({
        id: r.id,
        name: r.branch_name,
        baseVersion: r.base_version,
        data: safeJson(r.data, {}),
        createdAt: r.created_at
      }));

      // 简单父子关系：baseVersion 指向另一个分支的 id
      const byId = new Map(nodes.map((n) => [n.id, { ...n, children: [] }]));
      const roots = [];
      for (const n of byId.values()) {
        if (n.baseVersion && byId.has(n.baseVersion)) {
          byId.get(n.baseVersion).children.push(n);
        } else {
          roots.push(n);
        }
      }

      res.json({ success: true, entityId, count: nodes.length, tree: roots, branches: nodes });
    } catch (e) {
      res.status(500).json({ error: e?.message || "读取分支树失败" });
    }
  },

  /** 分支合并 */
  async mergeBranch(req, res) {
    try {
      const { entityId, sourceBranch, targetBranch } = req.body || {};
      const src = db.prepare("SELECT * FROM memory_branches WHERE id = ? AND entity_id = ?").get(sourceBranch, entityId);
      const tgt = db.prepare("SELECT * FROM memory_branches WHERE id = ? AND entity_id = ?").get(targetBranch, entityId);

      if (!src || !tgt) return res.status(404).json({ error: "源分支或目标分支不存在" });
      if (sourceBranch === targetBranch) return res.status(400).json({ error: "不能与自身合并" });

      const srcData = safeJson(src.data, {});
      const tgtData = safeJson(tgt.data, {});

      // 三方合并：两边都改且值不同 → 记冲突
      const srcBase = safeJson(src.base_version ? null : null, null);
      const merged = { ...tgtData };
      const conflicts = [];

      for (const [k, v] of Object.entries(srcData)) {
        if (!(k in tgtData)) merged[k] = v;
        else if (tgtData[k] !== v) conflicts.push({ key: k, source: v, target: tgtData[k] });
      }

      db.prepare("UPDATE memory_branches SET data = ?, base_version = ?, branch_name = ? WHERE id = ?")
        .run(JSON.stringify(merged), sourceBranch, `${tgt.branch_name}+${src.branch_name}`, targetBranch);

      res.json({
        success: true,
        entityId,
        targetBranch,
        mergedKeys: Object.keys(merged).length,
        conflicts
      });
    } catch (e) {
      res.status(500).json({ error: e?.message || "分支合并失败" });
    }
  },

  /** 版本回滚 */
  async rollbackBranch(req, res) {
    try {
      const { entityId, targetVersion } = req.body || {};
      const target = db.prepare(
        "SELECT * FROM memory_branches WHERE id = ? AND entity_id = ?"
      ).get(targetVersion, entityId);

      if (!target) return res.status(404).json({ error: "目标版本不存在" });

      // 备份当前状态，再指向目标版本
      const backupId = `branch-${uuidv4()}`;
      const current = db.prepare(
        "SELECT * FROM memory_branches WHERE entity_id = ? ORDER BY created_at DESC LIMIT 1"
      ).get(entityId);

      if (current && current.id !== targetVersion) {
        db.prepare(`
          INSERT INTO memory_branches (id, entity_id, branch_name, base_version, data, created_at)
          VALUES (?, ?, ?, ?, ?, ?)
        `).run(backupId, entityId, `pre-rollback-${Date.now().toString(36)}`, current.id, current.data, now());
      }

      res.json({
        success: true,
        entityId,
        rolledBackTo: targetVersion,
        data: safeJson(target.data, {}),
        backupId: current && current.id !== targetVersion ? backupId : null
      });
    } catch (e) {
      res.status(500).json({ error: e?.message || "版本回滚失败" });
    }
  }
};