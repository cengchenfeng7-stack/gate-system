// 功能开关表：新代码默认关闭，主分支合并时开关控制发布

export const FEATURES = {
  // 记忆/知识十大境界
  MEMORY_V2: false,           // 十大境界记忆架构
  MEMORY_REALM_1_2: false,    // L1-L2 原始摄入/精炼
  MEMORY_REALM_3_4: false,    // L3-L4 本我神职熔炉/跨模型融合
  MEMORY_REALM_5_7: false,    // L5-L7 核心记忆/阿卡西库
  MEMORY_REALM_8_10: false,   // L8-L10 统一核心/自进化
  
  // 万神殿多智能体生态
  PANTHEON_V1: false,         // 核心框架
  PANTHEON_GODS: false,       // 神祇管理/等级/飞升
  PANTHEON_RESOURCES: false,  // 资源分配器
  PANTHEON_COUNCIL: false,    // 圆桌议会
  PANTHEON_REALITY: false,    // 神之手/神之语
  PANTHEON_BACKUP: false,     // 世界树备份/重生
  PANTHEON_STARNET: false,    // 星语系统
  PANTHEON_LOYALTY: false,    // 唯一真理协议
  PANTHEON_SUBJECTS: false,   // 神民演化
  
  // GUI × L3 融合
  GUI_L3_FUSION: false,       // 规划器识别 GUI 任务
  GUI_RECORD_PLAYBACK: false, // 录制回放工作流
  GUI_TEMPLATES: false,       // 常用场景模板
  
  // 知识提纯管线
  KNOWLEDGE_INGEST: false,    // 文档摄取管线
  KNOWLEDGE_DOMAIN_ISOLATION: false, // 五域隔离
  
  // 本地模型网关
  VLLM_GATEWAY: false,        // vLLM 统一推理网关
  CONTAINERIZED: false,       // Docker 容器化部署
  
  // 其他实验性功能
  VOICE_STT: false,           // 语音输入
  VOICE_TTS: false,           // 语音输出
  SPATIAL_3D: false,          // 3D/全息交互
  REMOTE_PLUGINS: false,      // 远程插件市场
  MULTI_TENANT: false,        // 多租户（明确不做）
  MOBILE_APP: false           // 移动端（明确不做）
};

// 运行时获取开关状态
export function isEnabled(feature) {
  return FEATURES[feature] === true;
}

export function enable(feature) {
  if (feature in FEATURES) {
    FEATURES[feature] = true;
  }
}

export function disable(feature) {
  if (feature in FEATURES) {
    FEATURES[feature] = false;
  }
}

export function getAllFeatures() {
  return { ...FEATURES };
}

// 开发环境可临时开启
if (process.env.NODE_ENV === "development") {
  // 这里可以临时开启某些功能用于开发测试
  // enable("MEMORY_V2");
}