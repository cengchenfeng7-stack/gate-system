import { api, setApiToken, clearApiToken } from './client';
import type {
  OpenApiConfig,
  OpenApiKey,
} from '../types/api';

export async function getOpenApiConfig(): Promise<OpenApiConfig> {
  return api.get('/open/config');
}

export async function saveOpenApiConfig(data: {
  enabled?: boolean;
  webhookUrl?: string;
  webhookEnabled?: boolean;
  addKey?: { name: string; permissions: ('read' | 'write' | 'admin')[] };
  removeKey?: string;
  updateKey?: { id: string; name?: string; permissions?: ('read' | 'write' | 'admin')[] };
  regenerateKey?: boolean;
}): Promise<OpenApiConfig> {
  return api.post('/open/config', data);
}

export async function testWebhook(): Promise<{ success: boolean; message: string }> {
  return api.post('/open/webhook/test');
}

export async function openHealth(): Promise<{ app: string; version: string; time: string }> {
  return api.get('/open/health');
}

export async function openChat(message: string, sessionId?: string, model?: string): Promise<{
  reply: string;
  sessionId: string | null;
  model: string;
  usage: any;
}> {
  return api.post('/open/chat', { message, sessionId, model });
}

export async function createOpenTask(sessionId: string, goal: string, allowWrites = false): Promise<{
  id: string;
  status: string;
}> {
  return api.post('/open/tasks', { sessionId, goal, allowWrites });
}

export async function getOpenTask(id: string): Promise<any> {
  return api.get(`/open/tasks/${id}`);
}

// 设置全局 Token（用于后续开放接口调用）
export function setOpenApiToken(token: string) {
  setApiToken(token);
}

export function clearOpenApiToken() {
  clearApiToken();
}