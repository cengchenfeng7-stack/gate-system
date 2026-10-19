// API 基础类型
export interface ApiResponse<T = unknown> {
  success: boolean;
  error?: string;
  data?: T;
}

export interface PaginatedResponse<T> extends ApiResponse<T[]> {
  total?: number;
  page?: number;
  pageSize?: number;
}

// 会话相关
export interface Session {
  id: string;
  title: string;
  model: string;
  mode: SessionMode;
  target_agent_id: string | null;
  proactive_enabled: number;
  context_memory_enabled: number;
  avatar: string;
  category: string;
  tags: string;
  intro: string;
  location: string;
  announcement: string;
  my_nickname: string;
  remark: string;
  pinned: number;
  muted: number;
  folded: number;
  saved: number;
  group_no: string | null;
  member_limit: number;
  join_policy: string;
  last_read_at: string | null;
  speech_mode: SpeechMode;
  updated_at: string;
  created_at: string;
  messageCount?: number;
  memberCount?: number;
  lastMessage?: string | null;
  lastMessageAt?: string | null;
  unreadCount?: number;
}

export type SessionMode = 'group' | 'private' | 'workflow' | 'proactive' | 'continuous' | 'creative' | 'analysis' | 'autonomous' | 'cross_platform';

export type SpeechMode = 'quiet' | 'standard' | 'active' | 'mention';

// 消息相关
export interface Message {
  id: string;
  session_id: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  model: string | null;
  tool_calls: string | null;
  agent_id: string | null;
  agent_name: string | null;
  agent_color: string | null;
  intent: string | null;
  reply_source: string | null;
  reply_to_id: string | null;
  recalled: number;
  recalled_at: string | null;
  created_at: string;
}

export interface MessageSearchResult {
  id: string;
  session_id: string;
  role: string;
  content: string;
  created_at: string;
  session_title: string;
  highlighted?: string;
}

// 智能体
export interface Agent {
  id: string;
  name: string;
  role: string;
  avatar: string;
  color: string;
  system_prompt: string;
  keywords: string;
  intents: string;
  enabled: number;
  sort: number;
  created_at: string;
  updated_at: string;
  permissions?: MemberPermissions;
}

export interface MemberPermissions {
  speak: boolean;
  tools: boolean;
  write: boolean;
  command: boolean;
  notes: boolean;
}

export interface SessionAgent extends Agent {
  enabled: boolean;
  permissions: MemberPermissions;
  joined_at: string;
  sort_order: number;
}

// 知识库
export interface FaqEntry {
  id: string;
  question: string;
  answer: string;
  category: string;
  keywords: string;
  hits: number;
  enabled: number;
  last_hit_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface FaqSearchResult extends FaqEntry {
  score: number;
}

// 模型与提供商
export interface ProviderPreset {
  key: string;
  name: string;
  baseUrl: string;
  modelsUrl: string;
  defaultModel: string;
  apiKey?: string;
  protocol: 'openai' | 'claude' | 'ollama';
  enabled: boolean;
  sort: number;
  icon?: string;
  color?: string;
  tags?: string[];
  description?: string;
}

export interface ModelInfo {
  id: string;
  name: string;
  object: string;
  created: number;
  owned_by: string;
  capabilities: ModelCapabilities;
}

export interface ModelCapabilities {
  text: boolean;
  vision: boolean;
  video: boolean;
  thinking: boolean;
  tools: boolean;
  embedding: boolean;
}

export interface ActiveProvider {
  key: string;
  name: string;
  baseUrl: string;
  model: string;
  hasApiKey: boolean;
  apiKeyMasked?: string;
}

// 设置
export interface ToolSettings {
  fileToolsEnabled: boolean;
  terminalEnabled: boolean;
  guiEnabled: boolean;
  screenCaptureEnabled: boolean;
  sandboxMode: number;
}

export interface Preferences {
  defaultIntelligenceLevel: number;
  maxAgents: number;
  defaultChatMode: SessionMode;
  defaultWorkDir: string;
  familyAccessEnabled: boolean;
  familyBindHost: string;
}

export interface PricingConfig {
  inputPrice: number;
  outputPrice: number;
  cachedPrice: number;
  currency: 'CNY' | 'USD';
}

export interface ModelRoutes {
  default: string;
  quick: string;
  translate: string;
  vision: string;
  coding: string;
}

export interface AppSettings {
  toolSettings: ToolSettings;
  preferences: Preferences;
  pricing: PricingConfig;
  modelRoutes: ModelRoutes;
  familyEnabled: boolean;
  familyPasswordHash?: string;
  version: string;
}

// 自主性 L2/L3
export interface ProactiveService {
  id: string;
  session_id: string;
  service_type: string;
  trigger_condition: string;
  action: string;
  status: 'pending' | 'executed' | 'dismissed';
  priority: number;
  weight: number;
  feedback: 'accepted' | 'dismissed' | null;
  feedback_at: string | null;
  created_at: string;
  executed_at: string | null;
}

export interface UserProfile {
  id: string;
  summary: string;
  topics: Array<{ word: string; count: number }>;
  intents: Array<{ name: string; count: number }>;
  sessionCount: number;
  messageCount: number;
  updatedAt: string;
}

export interface StrategyLibraryEntry {
  id: string;
  name: string;
  goalPattern: string;
  keywords: string[];
  steps: Array<{ name: string; action: string }>;
  avgScore: number;
  useCount: number;
  sourceTask: string | null;
  createdAt: string;
  updatedAt: string;
  // v2.9 策略效果回归
  reuseCount: number;
  reuseSuccessCount: number;
  reuseAvgScore: number;
  reuseTotalDurationMs: number;
  lastReusedAt: string | null;
}

export interface AutonomousTask {
  id: string;
  session_id: string | null;
  goal: string;
  plan: Array<{ name: string; action: string }> | null;
  step_results: Array<TaskStepResult> | null;
  total_steps: number;
  current_step: number;
  status: 'queued' | 'running' | 'paused' | 'completed' | 'failed';
  last_error: string | null;
  priority: number;
  due_at: string | null;
  goal_id: string | null;
  model: string | null;
  allow_writes: number;
  replan_count: number;
  auto_retry: number;
  max_retry: number;
  timeout_sec: number;
  origin: string;
  recurrence: 'none' | 'daily' | 'weekly' | 'monthly';
  recur_time: string;
  recur_weekday: number;
  recur_monthday: number;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  matched_strategy_ids?: string[];
  task_start_time?: number;
}

export interface TaskStepResult {
  step: number;
  name: string;
  verified: boolean;
  score: number;
  output: string;
  error?: string;
  tools?: string[];
}

export interface AgentGoal {
  id: string;
  session_id: string | null;
  title: string;
  description: string;
  acceptance: string;
  deadline: string | null;
  status: 'active' | 'completed' | 'paused' | 'cancelled';
  progress: number;
  priority: number;
  last_review: string | null;
  last_review_result: { passed: boolean; reason: string; progress: number } | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface PatrolRun {
  id: string;
  started_at: string;
  finished_at: string;
  sessions_scanned: number;
  suggestions_created: number;
  auto_requeued: number;
  note: string;
}

export interface AutonomyStatus {
  patrol: {
    enabled: boolean;
    intervalMinutes: number;
    running: boolean;
    lastRun: PatrolRun | null;
  };
  queue: {
    runningTaskId: string | null;
    queued: number;
    running: number;
    paused: number;
    completed: number;
    failed: number;
  };
  profile: UserProfile | null;
  strategies: StrategyLibraryEntry[];
  goals: AgentGoal[];
}

// 审计与用量
export interface AuditLog {
  id: string;
  session_id: string | null;
  agent_id: string | null;
  agent_name: string | null;
  mode: string | null;
  tool_name: string;
  tool_input: string;
  tool_output: string;
  status: 'success' | 'failed' | 'pending_confirmation' | 'denied';
  duration_ms: number;
  created_at: string;
}

export interface TokenUsage {
  id: string;
  session_id: string | null;
  agent_id: string | null;
  mode: string | null;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cached_tokens: number;
  context_messages: number;
  context_chars: number;
  estimated: number;
  created_at: string;
}

export interface UsageOverview {
  summary: {
    totalCost: number;
    requestCount: number;
    totalTokens: number;
    cacheHitRate: number;
    activeDays: number;
    peakDay: { date: string; tokens: number } | null;
    topModel: string;
    avgDailyTokens: number;
  };
  daily: Array<{
    date: string;
    requests: number;
    promptTokens: number;
    completionTokens: number;
    totalTokens: number;
    cachedTokens: number;
    cost: number;
  }>;
  modelDistribution: Array<{
    model: string;
    requests: number;
    tokens: number;
    cachedTokens: number;
    cost: number;
  }>;
}

// 开放 API
export interface OpenApiKey {
  id: string;
  name: string;
  key: string;
  keyFull: string;
  permissions: ('read' | 'write' | 'admin')[];
  createdAt: string;
  lastUsed: string | null;
}

export interface OpenApiConfig {
  enabled: boolean;
  keys: OpenApiKey[];
  webhookUrl: string;
  webhookEnabled: boolean;
}

// 笔记
export interface Note {
  id: string;
  title: string;
  content_md: string;
  tags: string[];
  owner: 'human' | 'agent';
  visibility: 'private' | 'shared' | 'agent';
  task_id: string | null;
  pinned: number;
  created_at: string;
  updated_at: string;
}

export type NoteVisibility = 'private' | 'shared' | 'agent';

// AI 工作日志
export interface JournalEntry {
  id: string;
  task_id: string | null;
  session_id: string | null;
  event_type: string;
  summary: string;
  details: string | null;
  artifacts: string | null;
  created_at: string;
}

// 编码搭档
export interface CodingAgent {
  key: string;
  name: string;
  version: string | null;
  installed: boolean;
  path: string | null;
}

export interface CodingRun {
  id: string;
  agent_key: string;
  goal: string;
  cwd: string | null;
  command: string;
  model: string | null;
  status: 'running' | 'completed' | 'failed' | 'stopped';
  output: string | null;
  exit_code: number | null;
  started_at: string;
  finished_at: string | null;
  note_id: string | null;
}

// 通知
export interface Notification {
  id: string;
  type: 'task_finished' | 'task_failed' | 'coding_finished' | 'coding_failed' | 'service_disconnected' | 'service_reconnected' | 'patrol_suggestion' | 'info';
  title: string;
  body: string | null;
  session_id: string | null;
  link: string | null;
  read: number;
  created_at: string;
}

// 工作流模板
export interface WorkflowTemplate {
  id: string;
  name: string;
  description: string;
  stages: string[];
  category: string;
  enabled: number;
  created_at: string;
  updated_at: string;
}

// 知识图谱
export interface GraphEntity {
  id: string;
  entity_name: string;
  entity_type: string;
  properties: string | null;
  created_at: string;
  updated_at: string;
}

export interface GraphEdge {
  id: string;
  source_entity_id: string;
  target_entity_id: string;
  relationship_type: string;
  properties: string | null;
  created_at: string;
}

// SSE 事件类型
export interface SSEEvent {
  type: string;
  [key: string]: unknown;
}

// 表单相关
export interface CreateSessionRequest {
  title: string;
  avatar?: string;
  category?: string;
  intro?: string;
  location?: string;
  memberIds?: string[];
}

export interface UpdateSessionRequest {
  title?: string;
  avatar?: string;
  category?: string;
  tags?: string;
  intro?: string;
  location?: string;
  announcement?: string;
  my_nickname?: string;
  remark?: string;
  pinned?: boolean;
  muted?: boolean;
  folded?: boolean;
  saved?: boolean;
  member_limit?: number;
  join_policy?: 'manual' | 'auto' | 'confirm';
  speech_mode?: SpeechMode;
}

export interface QueueTaskRequest {
  sessionId: string;
  goal: string;
  priority?: number;
  dueAt?: string | null;
  goalId?: string | null;
  model?: string | null;
  allowWrites?: boolean;
  recurrence?: 'none' | 'daily' | 'weekly' | 'monthly';
  recurTime?: string;
  recurWeekday?: number;
  recurMonthday?: number;
  maxRetry?: number;
  timeoutSec?: number;
}

export interface StrategyEvaluateRequest {
  minReuseCount?: number;
  minSuccessRate?: number;
  minAvgScore?: number;
}

// 工具页
export interface TranslateRequest {
  text: string;
  targetLang: string;
}

export interface ScreenshotRequest {
  fullscreen?: boolean;
  windowId?: string;
}

export interface TextActionRequest {
  text: string;
  action: 'translate' | 'explain' | 'summarize';
}