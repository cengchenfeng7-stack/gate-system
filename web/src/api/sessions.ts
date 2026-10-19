import { api, ApiError } from './client';
import type {
  Session,
  SessionMode,
  SpeechMode,
  Message,
  MessageSearchResult,
  Agent,
  SessionAgent,
  FaqEntry,
  FaqSearchResult,
  CreateSessionRequest,
  UpdateSessionRequest,
  MemberPermissions,
} from '../types/api';

// 会话列表
export async function getSessions(): Promise<Session[]> {
  const res = await api.get<{ sessions: Session[] }>('/sessions');
  return res.sessions || [];
}

export async function getSession(id: string): Promise<{ session: Session; messages: Message[] }> {
  return api.get(`/sessions/${id}`);
}

export async function createSession(data: CreateSessionRequest): Promise<Session> {
  return api.post<Session>('/sessions', data);
}

export async function updateSession(id: string, data: UpdateSessionRequest): Promise<Session> {
  return api.patch<Session>(`/sessions/${id}`, data);
}

export async function deleteSession(id: string): Promise<void> {
  await api.delete(`/sessions/${id}`);
}

export async function markSessionRead(id: string): Promise<void> {
  await api.post(`/sessions/${id}/read`);
}

export async function expandSessionMembers(id: string, limit: number): Promise<{ memberLimit: number }> {
  return api.post(`/sessions/${id}/members/expand`, { limit });
}

// 会话消息搜索
export async function searchMessages(query: string, sessionId?: string, limit = 50): Promise<MessageSearchResult[]> {
  const params: Record<string, string | number> = { q: query, limit };
  if (sessionId) params.sessionId = sessionId;
  const res = await api.get<{ results: MessageSearchResult[] }>('/messages/search', params);
  return res.results || [];
}

// 群成员管理
export async function getSessionMembers(sessionId: string): Promise<{
  members: SessionAgent[];
  memberCount: number;
  memberLimit: number;
  joinPolicy: string;
}> {
  return api.get(`/sessions/${sessionId}/members`);
}

export async function addSessionMember(sessionId: string, agentId: string): Promise<{ members: SessionAgent[] }> {
  return api.post(`/sessions/${sessionId}/members`, { agentId });
}

export async function updateSessionMember(
  sessionId: string,
  agentId: string,
  patch: { enabled?: boolean; permissions?: Partial<MemberPermissions>; sortOrder?: number }
): Promise<{ success: boolean; enabled: boolean; permissions: MemberPermissions; sortOrder: number }> {
  return api.patch(`/sessions/${sessionId}/members/${agentId}`, patch);
}

export async function removeSessionMember(sessionId: string, agentId: string): Promise<{ success: boolean }> {
  return api.delete(`/sessions/${sessionId}/members/${agentId}`);
}

// 群文件
export interface GroupFile {
  id: string;
  sessionId: string;
  name: string;
  storedPath: string;
  size: number;
  mime: string;
  uploadedBy: string;
  createdAt: string;
  url: string;
}

export async function getGroupFiles(sessionId: string): Promise<GroupFile[]> {
  const res = await api.get<{ files: GroupFile[] }>(`/sessions/${sessionId}/files`);
  return res.files || [];
}

export async function uploadGroupFile(sessionId: string, name: string, base64: string, mime: string): Promise<GroupFile> {
  const res = await api.post<{ file: GroupFile }>(`/sessions/${sessionId}/files`, { name, base64, mime });
  return res.file;
}

export async function downloadGroupFile(sessionId: string, fileId: string): Promise<Blob> {
  const response = await fetch(`/api/sessions/${sessionId}/files/${fileId}`, { credentials: 'include' });
  if (!response.ok) throw new ApiError('下载失败', response.status);
  return response.blob();
}

export async function deleteGroupFile(sessionId: string, fileId: string): Promise<void> {
  await api.delete(`/sessions/${sessionId}/files/${fileId}`);
}

// 引用回复
export async function getReplySource(messageId: string): Promise<Message | null> {
  try {
    return await api.get(`/messages/${messageId}/reply-source`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

// 群公告
export async function getAnnouncement(sessionId: string): Promise<string | null> {
  try {
    const res = await api.get<{ announcement: string }>(`/sessions/${sessionId}/announcement`);
    return res.announcement;
  } catch {
    return null;
  }
}