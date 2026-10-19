import { api } from './client';
import type { SSEEvent } from '../types/api';

interface ChatOptions {
  sessionId: string;
  message: string;
  mode: string;
  model?: string;
  images?: string[] | null;
  replyToId?: string;
  allowWrites?: boolean;
  onEvent: (event: SSEEvent) => void;
  onError?: (error: Error) => void;
  onComplete?: () => void;
}

export async function sendChat(options: ChatOptions): Promise<void> {
  const { sessionId, message, mode, model, images, replyToId, allowWrites, onEvent, onError, onComplete } = options;
  
  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      credentials: 'include',
      body: JSON.stringify({
        sessionId,
        message,
        mode,
        model,
        images,
        replyToId,
        allowWrites,
      }),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ error: '请求失败' }));
      throw new Error(errorData.error || `HTTP ${response.status}`);
    }

    const reader = response.body?.getReader();
    if (!reader) return;

    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader!.read();
      if (done) break;
      
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6));
            onEvent(data);
          } catch {
            // 忽略解析错误
          }
        }
      }
    }
    
    onComplete?.();
  } catch (error) {
    onError?.(error as Error);
  }
}

// 翻译工具
export async function translateText(text: string, targetLang: string): Promise<{
  translated: string;
  tokens: number;
}> {
  const res = await api.post<{ translated: string; tokens: number }>('/tools/translate', { text, targetLang });
  return res;
}

// 划词操作
export async function textAction(text: string, action: 'translate' | 'explain' | 'summarize'): Promise<{
  result: string;
  tokens: number;
}> {
  const res = await api.post<{ result: string; tokens: number }>('/tools/text-action', { text, action });
  return res;
}

// 截图
export async function takeScreenshot(): Promise<{ filename: string; url: string }> {
  return api.post('/tools/screenshot');
}

export async function getRecentScreenshots(): Promise<Array<{ filename: string; url: string; createdAt: string }>> {
  const res = await api.get<{ screenshots: Array<{ filename: string; url: string; createdAt: string }> }>('/tools/screenshots');
  return res.screenshots || [];
}

// 工作区文件
export async function getWorkspaceFiles(): Promise<Array<{ name: string; path: string; size: number }>> {
  const res = await api.get<{ files: Array<{ name: string; path: string; size: number }> }>('/workspace/files');
  return res.files || [];
}

export async function readWorkspaceFile(path: string): Promise<{ content: string }> {
  return api.get('/workspace/file', { path });
}