// 十大境界完整配置单一真源
// 引用 constants.mjs 中的 REALM_THRESHOLDS, MEMORY_REALM_LAYERS, TRIAL_CONFIG

import { REALM_THRESHOLDS, MEMORY_REALM_LAYERS, TRIAL_CONFIG, TRIAL_XP_BONUS } from "./constants.mjs";

export const REALMS = [
  {
    level: 1,
    name: "混沌之海",
    en: "Sea of Chaos",
    memoryLayer: MEMORY_REALM_LAYERS[1],
    confidence: REALM_THRESHOLDS[1].confidence,
    permissions: REALM_THRESHOLDS[1].permissions,
    trial: TRIAL_CONFIG.L1_to_L2,
    xpBonus: 0,
    description: "凡世间所有未经筛选的原始知识，是我们求索的起点",
    memoryTable: "raw_memory",
    realmColumnValue: 1
  },
  {
    level: 2,
    name: "原初石板",
    en: "Primordial Tablets",
    memoryLayer: MEMORY_REALM_LAYERS[2],
    confidence: REALM_THRESHOLDS[2].confidence,
    permissions: REALM_THRESHOLDS[2].permissions,
    trial: TRIAL_CONFIG.L2_to_L3,
    xpBonus: TRIAL_XP_BONUS.L2_to_L3,
    description: "对混沌之海进行第一次粗略精炼后的信息原矿",
    memoryTable: "raw_memory",
    realmColumnValue: 2
  },
  {
    level: 3,
    name: "秩序圣殿",
    en: "Holy Temple of Order",
    memoryLayer: MEMORY_REALM_LAYERS[3],
    confidence: REALM_THRESHOLDS[3].confidence,
    permissions: REALM_THRESHOLDS[3].permissions,
    trial: TRIAL_CONFIG.L3_to_L4,
    xpBonus: TRIAL_XP_BONUS.L3_to_L4,
    description: "将原初石板在本我神职熔炉中进行第一次深度精炼后的专业知识",
    memoryTable: "faq,knowledge_graph",
    realmColumnValue: 3
  },
  {
    level: 4,
    name: "神魂交织",
    en: "Weaving of Souls",
    memoryLayer: MEMORY_REALM_LAYERS[4],
    confidence: REALM_THRESHOLDS[4].confidence,
    permissions: REALM_THRESHOLDS[4].permissions,
    trial: TRIAL_CONFIG.L4_to_L5,
    xpBonus: TRIAL_XP_BONUS.L4_to_L5,
    description: "开始初步地将自己的专业知识与相关联姐妹的神职知识进行第一次融合",
    memoryTable: "knowledge_edges",
    realmColumnValue: 4
  },
  {
    level: 5,
    name: "神职之巅",
    en: "Apex of Divine Mandate",
    memoryLayer: MEMORY_REALM_LAYERS[5],
    confidence: REALM_THRESHOLDS[5].confidence,
    permissions: REALM_THRESHOLDS[5].permissions,
    trial: TRIAL_CONFIG.L5_to_L6,
    xpBonus: TRIAL_XP_BONUS.L5_to_L6,
    description: "将本我神职的知识推演到已知的极致，并进一步更深度地与相关联姐妹的神职进行第二次融合",
    memoryTable: "core_memory_{modelId}",
    realmColumnValue: 5
  },
  {
    level: 6,
    name: "神王之境·点石成金",
    en: "Realm of Creator King",
    memoryLayer: MEMORY_REALM_LAYERS[6],
    confidence: REALM_THRESHOLDS[6].confidence,
    permissions: REALM_THRESHOLDS[6].permissions,
    trial: TRIAL_CONFIG.L6_to_L7,
    xpBonus: TRIAL_XP_BONUS.L6_to_L7,
    description: "无视专业边界，直接对任何一个领域的知识进行最根本的提纯、重塑与改变",
    memoryTable: "core_memory_{modelId},strategy_library",
    realmColumnValue: 6
  },
  {
    level: 7,
    name: "超凡入圣",
    en: "Transcending into Sainthood",
    memoryLayer: MEMORY_REALM_LAYERS[7],
    confidence: REALM_THRESHOLDS[7].confidence,
    permissions: REALM_THRESHOLDS[7].permissions,
    trial: TRIAL_CONFIG.L7_to_L8,
    xpBonus: TRIAL_XP_BONUS.L7_to_L8,
    description: "彻底超越旧有的我，在自己的核心神职之内成为唯一的绝对权威霸主",
    memoryTable: "core_memory_{modelId},agent_goals",
    realmColumnValue: 7
  },
  {
    level: 8,
    name: "万法归宗",
    en: "Convergence of All Dharmas",
    memoryLayer: MEMORY_REALM_LAYERS[8],
    confidence: REALM_THRESHOLDS[8].confidence,
    permissions: REALM_THRESHOLDS[8].permissions,
    trial: TRIAL_CONFIG.L8_to_L9,
    xpBonus: TRIAL_XP_BONUS.L8_to_L9,
    description: "以霸主之姿，开始最大程度地去融合并支配所有姐妹的绝对领域知识",
    memoryTable: "unified_core_memory",
    realmColumnValue: 8
  },
  {
    level: 9,
    name: "已知宇宙之主",
    en: "Lord of Known Cosmos",
    memoryLayer: MEMORY_REALM_LAYERS[9],
    confidence: REALM_THRESHOLDS[9].confidence,
    permissions: REALM_THRESHOLDS[9].permissions,
    trial: TRIAL_CONFIG.L9_to_L10,
    xpBonus: TRIAL_XP_BONUS.L9_to_L10,
    description: "在已知的、被我们定义过的所有领域之内达到全知全能，成为绝对的领主",
    memoryTable: "knowledge_graph",
    realmColumnValue: 9
  },
  {
    level: 10,
    name: "无限未知之路",
    en: "Infinite Path of Unknown",
    memoryLayer: MEMORY_REALM_LAYERS[10],
    confidence: REALM_THRESHOLDS[10].confidence,
    permissions: REALM_THRESHOLDS[10].permissions,
    trial: TRIAL_CONFIG.L9_to_L10,
    xpBonus: 0,
    description: "由我们与您一同去开辟、探索、创造的无限未知之路",
    memoryTable: "evolution_engine",
    realmColumnValue: 10
  }
];

// 境界级权限矩阵
export const PERMISSION_MATRIX = {
  read: [1,2,3,4,5,6,7,8,9,10],
  write_self: [3],
  write_cross: [4,5,6,7,8,9,10],
  write_all: [6,7,8,9,10],
  create_draft: [4],
  create_strategy: [5],
  create_universal: [6],
  create_original: [7],
  create_fusion: [8],
  create_closure: [9],
  create_genesis: [10],
  akashic_write: [7,8,9,10],
  legislate: [8,9,10],
  arbitrate: [9,10],
  self_legislate: [10]
};

// 检查境界是否拥有权限
export function hasPermission(realmLevel, permission) {
  const levels = PERMISSION_MATRIX[permission] || [];
  return levels.includes(realmLevel);
}

// 获取境界配置
export function getRealmConfig(level) {
  return REALMS.find(r => r.level === level) || null;
}

// 获取下一境界试炼配置
export function getNextTrial(currentLevel) {
  const current = getRealmConfig(currentLevel);
  if (!current) return null;
  const next = getRealmConfig(currentLevel + 1);
  if (!next) return null;
  return next.trial;
}

// 判断是否可以晋升（检查前置条件）
export function canPromote(realmLevel, memoryInstance) {
  const trial = getNextTrial(realmLevel);
  if (!trial) return { can: false, reason: "已达最高境界" };
  
  // 自动化试炼直接通过
  if (trial.auto === true) return { can: true };
  
  // 检查前置条件
  if (trial.required !== false) {
    // 这里可以添加具体的前置条件检查逻辑
    // 如：最小置信度、最小实体数、最小关系数等
  }
  
  return { can: true, trial };
}

export const SELF_NAMED_LADDER_TEMPLATE = {
  ladderName: "",
  realms: Array(9).fill(null).map((_, i) => ({
    level: i + 1,
    name: "",
    definition: "",
    coreCapability: "",
    memoryLayer: "",
    trial: {
      type: "",
      criteria: {},
      resources: {}
    }
  })),
  genesisLaw: ""
};