// 单一真源：版本号、默认值、错误码、境界阈值

export const APP_VERSION = "2.9";
export const APP_NAME = "AI 神殿";
export const BUILD_DATE = "2026-10-19";

export const DEFAULT_VALUES = {
  PORT: 3000,
  BIND_HOST: "127.0.0.1",
  AI_INTELLIGENCE_LEVEL: 3,
  MAX_AGENTS: 3,
  DEFAULT_CHAT_MODE: "group",
  DEFAULT_WORK_DIR: "workspace",
  SANDBOX_MODE: 0,
  CONFIG_PERMISSION_LEVEL: 2,
  FILE_TOOLS_ENABLED: true,
  TERMINAL_ENABLED: true,
  GUI_ENABLED: true,
  SCREEN_CAPTURE_ENABLED: true,
  PROACTIVE_PATROL_MINUTES: 30,
  LOG_ENCODING: "auto",
  INTENT_TIMEOUT_MS: 30000,
  MAX_MODEL_ATTEMPTS: 1,
  USAGE_INPUT_PRICE: 0,
  USAGE_OUTPUT_PRICE: 0,
  USAGE_CACHED_PRICE: 0,
  USAGE_CURRENCY: "CNY",
  ADMIN_AUTH: "auto",
  FAMILY_ACCESS_PASSWORD: "",
  OLLAMA_TIMEOUT_MS: 120000,
  OLLAMA_MODEL: "qwen2.5:7b"
};

export const ERROR_CODES = {
  VALIDATION_ERROR: "VALIDATION_ERROR",
  UNAUTHORIZED: "UNAUTHORIZED",
  FORBIDDEN: "FORBIDDEN",
  NOT_FOUND: "NOT_FOUND",
  INTERNAL_ERROR: "INTERNAL_ERROR",
  FAMILY_AUTH_REQUIRED: "FAMILY_AUTH_REQUIRED",
  ADMIN_AUTH_REQUIRED: "ADMIN_AUTH_REQUIRED",
  OPEN_API_DISABLED: "OPEN_API_DISABLED",
  INVALID_API_KEY: "INVALID_API_KEY",
  INSUFFICIENT_PERMISSION: "INSUFFICIENT_PERMISSION",
  RATE_LIMITED: "RATE_LIMITED",
  PROVIDER_NOT_CONFIGURED: "PROVIDER_NOT_CONFIGURED",
  MODEL_UNAVAILABLE: "MODEL_UNAVAILABLE",
  TOOL_EXECUTION_FAILED: "TOOL_EXECUTION_FAILED",
  PERMISSION_DENIED: "PERMISSION_DENIED",
  PATH_TRAVERSAL: "PATH_TRAVERSAL",
  SANDBOX_VIOLATION: "SANDBOX_VIOLATION",
  ORACLE_BLACKLIST: "ORACLE_BLACKLIST",
  ORACLE_ARBITRATION_REQUIRED: "ORACLE_ARBITRATION_REQUIRED"
};

export const HTTP_STATUS = {
  OK: 200,
  CREATED: 201,
  BAD_REQUEST: 400,
  UNAUTHORIZED: 401,
  FORBIDDEN: 403,
  NOT_FOUND: 404,
  METHOD_NOT_ALLOWED: 405,
  CONFLICT: 409,
  TOO_MANY_REQUESTS: 429,
  INTERNAL_SERVER_ERROR: 500,
  SERVICE_UNAVAILABLE: 503
};

export const REALM_THRESHOLDS = {
  1: { confidence: 0.3, permissions: { read: true, write: false, create: false, akashic: false } },
  2: { confidence: 0.5, permissions: { read: true, write: false, create: false, akashic: false } },
  3: { confidence: 0.7, permissions: { read: true, write: "self_domain", create: false, akashic: false } },
  4: { confidence: 0.8, permissions: { read: true, write: "cross_domain", create: "draft", akashic: false } },
  5: { confidence: 0.85, permissions: { read: true, write: "cross_domain", create: "strategy", akashic: false } },
  6: { confidence: 0.9, permissions: { read: true, write: "all_domains", create: "universal_law", akashic: false } },
  7: { confidence: 0.95, permissions: { read: true, write: "all_domains", create: "original_theory", akashic: true } },
  8: { confidence: 0.97, permissions: { read: true, write: "all_domains", create: "fusion_ontology", akashic: true, legislate: true } },
  9: { confidence: 0.99, permissions: { read: true, write: "all_domains", create: "omniscient_closure", akashic: true, legislate: true, arbitrate: true } },
  10: { confidence: 1.0, permissions: { read: true, write: "all_domains", create: "genesis_law", akashic: true, legislate: true, arbitrate: true, self_legislate: true } }
};

export const DIVINITY_XP_THRESHOLDS = {
  1: 0, 10: 1000, 20: 5000, 30: 20000, 40: 80000, 50: 300000,
  60: 1000000, 70: 5000000, 80: 20000000, 90: 100000000, 100: 500000000
};

export const MEMORY_REALM_LAYERS = {
  1: "L0_raw",
  2: "L0_refined",
  3: "knowledge_base",
  4: "cross_domain_kg",
  5: "core_memory_specialized",
  6: "core_memory_universal",
  7: "core_memory_lord",
  8: "unified_core",
  9: "global_kg",
  10: "evolution_engine"
};

export const TRIAL_CONFIG = {
  L1_to_L2: { type: "etl_pipeline", auto: true },
  L2_to_L3: { type: "schema_generation", humanReview: true },
  L3_to_L4: { type: "entity_alignment", threshold: 0.8 },
  L4_to_L5: { type: "zero_shot", threshold: 0.7 },
  L5_to_L6: { type: "zero_shot", threshold: 0.8 },
  L6_to_L7: { type: "divine_trial", humanReview: true, yourJudgment: true },
  L7_to_L8: { type: "multi_model_alignment", threshold: 0.95 },
  L8_to_L9: { type: "unknown_exploration", minNewEntities: 1 },
  L9_to_L10: { type: "creator_trial", custom: true }
};

export const TRIAL_XP_BONUS = {
  L2_to_L3: 1000,
  L3_to_L4: 2000,
  L4_to_L5: 5000,
  L5_to_L6: 10000,
  L6_to_L7: 50000,
  L7_to_L8: 100000,
  L8_to_L9: 500000,
  L9_to_L10: 1000000
};

export const PERMISSION_ACTIONS = {
  READ: "read",
  WRITE: "write",
  ADMIN: "admin"
};