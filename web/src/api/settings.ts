import { api } from './client';
import type {
  ProviderPreset,
  ActiveProvider,
  ModelInfo,
  ToolSettings,
  Preferences,
  PricingConfig,
  ModelRoutes,
  AppSettings,
} from '../types/api';

// 提供商与模型
export async function getProviders(): Promise<{ presets: Record<string, ProviderPreset>; activeKey: string }> {
  return api.get('/providers/config');
}

export async function getActiveProvider(): Promise<ActiveProvider> {
  return api.get('/provider');
}

export async function getModels(): Promise<{ models: ModelInfo[] }> {
  return api.get('/models');
}

export async function fetchModels(baseUrl: string, apiKey?: string): Promise<{ models: ModelInfo[] }> {
  return api.post('/models/fetch', { baseUrl, apiKey });
}

export async function saveProviderConfig(data: {
  providerKey: string;
  apiKey?: string;
  baseUrl?: string;
  model?: string;
  activate?: boolean;
}): Promise<{ success: boolean; activeKey: string }> {
  return api.post('/providers/config', data);
}

export async function getProviderDetail(key: string): Promise<{ preset: ProviderPreset; activeConfig: ActiveProvider }> {
  return api.get(`/providers/detail?key=${encodeURIComponent(key)}`);
}

// 权限
export async function getPermissionLevel(): Promise<{ level: number }> {
  return api.get('/config-permission');
}

export async function setPermissionLevel(level: number): Promise<void> {
  await api.post('/config-permission', { permissionLevel: level });
}

// 环境配置
export async function saveEnvConfig(data: Record<string, string>): Promise<void> {
  await api.post('/save-env-config', data);
}

// 工具设置
export async function getToolSettings(): Promise<ToolSettings> {
  return api.get('/settings/tools');
}

export async function saveToolSettings(settings: ToolSettings): Promise<void> {
  await api.post('/settings/tools', settings);
}

// 偏好设置
export async function getPreferences(): Promise<Preferences> {
  return api.get('/settings/preferences');
}

export async function savePreferences(prefs: Preferences): Promise<void> {
  await api.post('/settings/preferences', prefs);
}

// 计费设置
export async function getPricing(): Promise<PricingConfig> {
  return api.get('/settings/pricing');
}

export async function savePricing(pricing: PricingConfig): Promise<void> {
  await api.post('/settings/pricing', pricing);
}

// 模型分工
export async function getModelRoutes(): Promise<ModelRoutes> {
  return api.get('/settings/model-routes');
}

export async function saveModelRoutes(routes: ModelRoutes): Promise<void> {
  await api.post('/settings/model-routes', routes);
}

// 家庭访问
export async function getFamilyStatus(): Promise<{
  enabled: boolean;
  bindHost: string;
}> {
  return api.get('/family/status');
}

export async function familyLogin(password: string): Promise<{ success: boolean }> {
  return api.post('/family/login', { password });
}

export async function familyLogout(): Promise<void> {
  await api.post('/family/logout');
}

export async function setFamilyConfig(config: { enabled: boolean; password?: string }): Promise<void> {
  await api.post('/settings/family', config);
}

// 完整设置
export async function getAllSettings(): Promise<AppSettings> {
  return api.get('/settings');
}

// 备份
export async function listBackups(): Promise<Array<{
  id: string;
  createdAt: string;
  auto: boolean;
  dbSize: number;
  workspaceSize: number;
  totalSize: number;
  workspace: boolean;
}>> {
  const res = await api.get<{ backups: any[] }>('/backups');
  return res.backups || [];
}

export async function createBackup(includeWorkspace = false): Promise<{ id: string }> {
  return api.post('/backups', { workspace: includeWorkspace });
}

export async function deleteBackup(id: string): Promise<void> {
  await api.delete(`/backups/${id}`);
}

export async function restoreBackup(id: string): Promise<void> {
  await api.post(`/backups/${id}/restore`, { confirm: true });
}

export async function setAutoBackup(enabled: boolean): Promise<void> {
  await api.post('/settings/backup', { enabled });
}

// 更新日志
export async function getChangelog(): Promise<Array<{
  version: string;
  date: string;
  features: string[];
  fixes: string[];
  verified: string[];
}>> {
  return api.get('/changelog');
}

// 用量统计
export async function getUsageOverview(days = 30): Promise<any> {
  return api.get(`/usage/overview?days=${days}`);
}

// 审计日志
export async function getAuditLogs(params?: { limit?: number; offset?: number; sessionId?: string }): Promise<{
  logs: any[];
  total: number;
}> {
  const searchParams = new URLSearchParams();
  if (params?.limit) searchParams.set('limit', String(params.limit));
  if (params?.offset) searchParams.set('offset', String(params.offset));
  if (params?.sessionId) searchParams.set('sessionId', params.sessionId);
  return api.get(`/audit-logs?${searchParams.toString()}`);
}

// 数据库
export async function getDbTables(): Promise<string[]> {
  const res = await api.get<{ tables: string[] }>('/db/tables');
  return res.tables || [];
}

export async function getDbTable(name: string, limit = 100, offset = 0): Promise<{
  columns: string[];
  rows: any[];
  total: number;
}> {
  return api.get(`/db/table/${encodeURIComponent(name)}?limit=${limit}&offset=${offset}`);
}

export async function runDbQuery(sql: string): Promise<{ columns: string[]; rows: any[] }> {
  return api.post('/db/query', { sql });
}

export async function dbMaintenance(): Promise<void> {
  await api.post('/db/maintenance');
}

// 知识库
export async function getFaqList(params?: { enabled?: boolean; category?: string; limit?: number; offset?: number }): Promise<{
  items: any[];
  total: number;
}> {
  const searchParams = new URLSearchParams();
  if (params?.enabled !== undefined) searchParams.set('enabled', String(params.enabled));
  if (params?.category) searchParams.set('category', params.category);
  if (params?.limit) searchParams.set('limit', String(params.limit));
  if (params?.offset) searchParams.set('offset', String(params.offset));
  return api.get(`/faq?${searchParams.toString()}`);
}

export async function searchFaq(query: string, limit = 10): Promise<any[]> {
  const res = await api.get<{ results: any[] }>(`/faq/search?q=${encodeURIComponent(query)}&limit=${limit}`);
  return res.results || [];
}

export async function getFaqStats(): Promise<{
  total: number;
  enabled: number;
  totalHits: number;
  ftsEnabled: boolean;
}> {
  return api.get('/faq/stats');
}

export async function createFaq(data: { question: string; answer: string; category?: string; keywords?: string }): Promise<any> {
  return api.post('/faq', data);
}

export async function updateFaq(id: string, data: Partial<{ question: string; answer: string; category: string; keywords: string; enabled: boolean }>): Promise<any> {
  return api.patch(`/faq/${id}`, data);
}

export async function deleteFaq(id: string): Promise<void> {
  await api.delete(`/faq/${id}`);
}

export async function importFaq(file: File): Promise<{ imported: number }> {
  const formData = new FormData();
  formData.append('file', file);
  const response = await fetch('/api/faq/import', {
    method: 'POST',
    body: formData,
    credentials: 'include',
  });
  if (!response.ok) throw new Error('导入失败');
  return response.json();
}

export async function exportFaq(): Promise<Blob> {
  const response = await fetch('/api/faq/export', { credentials: 'include' });
  if (!response.ok) throw new Error('导出失败');
  return response.blob();
}

export async function rebuildFts(): Promise<void> {
  await api.post('/faq/rebuild-fts');
}

// 工作流模板
export async function getWorkflowTemplates(): Promise<Array<{
  id: string;
  name: string;
  description: string;
  stages: string[];
  category: string;
  enabled: boolean;
  createdAt: string;
  updatedAt: string;
}>> {
  const res = await api.get<{ templates: any[] }>('/workflow-templates');
  return res.templates || [];
}

export async function saveWorkflowTemplate(data: {
  name: string;
  description?: string;
  stages: string[];
  category?: string;
}): Promise<any> {
  return api.post('/workflow-templates', data);
}

export async function deleteWorkflowTemplate(id: string): Promise<void> {
  await api.delete(`/workflow-templates/${id}`);
}

// 提示词
export async function getPromptConfig(): Promise<{
  system_prompt: string;
  step_prompt: string;
  style_prompt: string;
  enforce: boolean;
}> {
  return api.get('/prompts?scope=global');
}

export async function savePromptConfig(data: {
  system_prompt?: string;
  step_prompt?: string;
  style_prompt?: string;
  enforce?: boolean;
}): Promise<void> {
  await api.post('/prompts', data);
}

// 智力档位
export async function getIntelligenceLevels(): Promise<Array<{
  level: number;
  name: string;
  maxTurns: number;
  temperature: number;
  description: string;
}>> {
  const res = await api.get<{ levels: any[] }>('/intelligence');
  return res.levels || [];
}

// Token 统计
export async function getTokenUsage(params?: { limit?: number; offset?: number }): Promise<{
  items: any[];
  total: number;
}> {
  const searchParams = new URLSearchParams();
  if (params?.limit) searchParams.set('limit', String(params.limit));
  if (params?.offset) searchParams.set('offset', String(params.offset));
  return api.get(`/token-usage?${searchParams.toString()}`);
}

// Agent 商店
export async function getStoreItems(kind?: string): Promise<Array<{
  id: string;
  kind: 'skill' | 'plugin' | 'mcp';
  name: string;
  description: string;
  config: any;
  enabled: boolean;
  source: string;
  createdAt: string;
  updatedAt: string;
}>> {
  const params = kind ? `?kind=${kind}` : '';
  const res = await api.get<{ items: any[] }>(`/store${params}`);
  return res.items || [];
}

export async function saveStoreItem(data: {
  kind: 'skill' | 'plugin' | 'mcp';
  name: string;
  description?: string;
  config: any;
  enabled?: boolean;
}): Promise<any> {
  return api.post('/store', data);
}

export async function updateStoreItem(id: string, data: Partial<any>): Promise<any> {
  return api.patch(`/store/${id}`, data);
}

export async function deleteStoreItem(id: string): Promise<void> {
  await api.delete(`/store/${id}`);
}

// MCP
export async function getMcpStatus(): Promise<Array<{
  id: string;
  name: string;
  command: string;
  args: string[];
  env: Record<string, string>;
  enabled: boolean;
  connected: boolean;
  tools: any[];
}>> {
  return api.get('/mcp/status');
}

export async function connectMcp(id: string): Promise<{ success: boolean }> {
  return api.post(`/mcp/${id}/connect`);
}

export async function disconnectMcp(id: string): Promise<{ success: boolean }> {
  return api.post(`/mcp/${id}/disconnect`);
}

export async function callMcpTool(id: string, toolName: string, args: any): Promise<any> {
  return api.post(`/mcp/${id}/call`, { tool: toolName, arguments: args });
}

// 沙箱
export async function getSandboxStatus(): Promise<{
  enabled: boolean;
  files: Array<{ path: string; size: number }>;
}> {
  return api.get('/sandbox/status');
}

export async function toggleSandbox(enabled: boolean): Promise<{ enabled: boolean }> {
  return api.post('/sandbox/toggle', { enabled });
}

export async function commitSandbox(): Promise<void> {
  await api.post('/sandbox/commit');
}

export async function discardSandbox(): Promise<void> {
  await api.post('/sandbox/discard');
}

// 知识图谱
export async function getGraph(): Promise<{
  entities: Array<{ id: string; entity_name: string; entity_type: string; properties: any }>;
  edges: Array<{ id: string; source_entity_id: string; target_entity_id: string; relationship_type: string; properties: any }>;
}> {
  return api.get('/graph');
}

export async function rebuildGraph(): Promise<{ entities: number; edges: number }> {
  return api.post('/graph/rebuild');
}

// 插件工具
export async function getPluginTools(): Promise<Array<{
  id: string;
  name: string;
  description: string;
  config: any;
  enabled: boolean;
}>> {
  const res = await api.get<{ tools: any[] }>('/plugins/tools');
  return res.tools || [];
}

export async function testPluginTool(id: string, toolName: string, args: any): Promise<any> {
  return api.post(`/plugins/${id}/test`, { tool: toolName, arguments: args });
}