import { api } from './client';
import type {
  ProactiveService,
  UserProfile,
  StrategyLibraryEntry,
  AutonomousTask,
  AgentGoal,
  PatrolRun,
  AutonomyStatus,
  TaskStepResult,
  QueueTaskRequest,
  StrategyEvaluateRequest,
} from '../types/api';

export async function getAutonomyStatus(): Promise<AutonomyStatus> {
  return api.get('/autonomy/status');
}

export async function runPatrol(): Promise<{ result: PatrolRun }> {
  return api.post('/autonomy/patrol');
}

export async function configurePatrol(config: { enabled?: boolean; intervalMinutes?: number }): Promise<{
  enabled: boolean;
  intervalMinutes: number;
}> {
  return api.post('/autonomy/patrol/config', config);
}

// 用户画像
export async function getProfile(): Promise<{ profile: UserProfile | null }> {
  return api.get('/profile');
}

export async function rebuildProfile(days = 30): Promise<{ profile: UserProfile }> {
  return api.post('/profile/rebuild', { days });
}

// 策略库
export async function getStrategies(limit = 50): Promise<{ strategies: StrategyLibraryEntry[] }> {
  return api.get(`/strategies?limit=${limit}`);
}

export async function deleteStrategy(id: string): Promise<{ success: boolean }> {
  return api.delete(`/strategies/${id}`);
}

export async function evaluateStrategies(params?: StrategyEvaluateRequest): Promise<{
  removed: number;
  kept: number;
  details: Array<{ id: string; name: string; action: string; reason: string }>;
}> {
  return api.post('/strategies/evaluate', params || {});
}

// 任务队列
export async function getAutonomousTasks(): Promise<{ tasks: AutonomousTask[] }> {
  return api.get('/autonomous/tasks');
}

export async function queueTask(data: QueueTaskRequest): Promise<{ task: AutonomousTask }> {
  return api.post('/autonomous/queue', data);
}

export async function retryTask(id: string): Promise<{ success: boolean }> {
  return api.post(`/autonomous/tasks/${id}/retry`);
}

export async function abandonTask(id: string): Promise<{ success: boolean }> {
  return api.post(`/autonomous/tasks/${id}/abandon`);
}

// 长程目标
export async function getGoals(status?: string): Promise<{ goals: AgentGoal[] }> {
  const params = status ? `?status=${status}` : '';
  return api.get(`/goals${params}`);
}

export async function createGoal(data: {
  title: string;
  description?: string;
  acceptance?: string;
  deadline?: string | null;
  sessionId?: string | null;
  priority?: number;
}): Promise<AgentGoal> {
  return api.post('/goals', data);
}

export async function updateGoal(id: string, data: Partial<AgentGoal>): Promise<AgentGoal> {
  return api.patch(`/goals/${id}`, data);
}

export async function deleteGoal(id: string): Promise<{ success: boolean }> {
  return api.delete(`/goals/${id}`);
}

export async function reviewGoal(id: string): Promise<{ goal: AgentGoal; verdict: { passed: boolean; reason: string } }> {
  return api.post(`/goals/${id}/review`);
}

// 建议处理
export async function getPendingProactive(): Promise<{ suggestions: ProactiveService[] }> {
  return api.get('/proactive/pending');
}

export async function resolveProactive(id: string, action: 'execute' | 'dismiss'): Promise<{ success: boolean }> {
  return api.post(`/proactive/${id}/resolve`, { action });
}