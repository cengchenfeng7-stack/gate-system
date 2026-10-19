import { api } from './client';
import type { Agent } from '../types/api';

export async function getAgents(): Promise<Agent[]> {
  const res = await api.get<{ agents: Agent[] }>('/agents');
  return res.agents || [];
}

export async function getAgent(id: string): Promise<Agent> {
  return api.get(`/agents/${id}`);
}

export async function createAgent(data: Partial<Agent>): Promise<Agent> {
  return api.post<Agent>('/agents', data);
}

export async function updateAgent(id: string, data: Partial<Agent>): Promise<Agent> {
  return api.patch<Agent>(`/agents/${id}`, data);
}

export async function deleteAgent(id: string): Promise<void> {
  await api.delete(`/agents/${id}`);
}