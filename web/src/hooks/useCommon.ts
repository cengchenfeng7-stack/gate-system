import { useState, useEffect, useRef, useCallback } from 'react';

/**
 * SSE 连接 Hook - 避免 esbuild 解析器 bug
 * 关键：将 while 循环提取到单独的 async 函数中，避免 esbuild 解析器 bug
 */
export function useSSE(
  url: string,
  onEvent: (event: any) => void,
  options?: {
    onError?: (error: Error) => void;
    onOpen?: () => void;
    onClose?: () => void;
    retryInterval?: number;
    maxRetries?: number;
    enabled?: boolean;
  }
) {
  const { onError, onOpen, onClose, retryInterval = 3000, maxRetries = 3, enabled = true } = options || {};
  const abortRef = useRef<AbortController | null>(null);
  const retryCountRef = useRef(0);
  const [connected, setConnected] = useState(false);

  // 核心连接循环 - 单独的 async 函数，避免 esbuild 解析器 bug
  const runConnectionLoop = async (
    controller: AbortController,
    ctx: {
      url: string;
      onEvent: (event: any) => void;
      onOpen?: () => void;
      onError?: (error: Error) => void;
      onClose?: () => void;
      retryInterval: number;
      maxRetries: number;
      enabled: boolean;
      retryCountRef: React.MutableRefObject<number>;
      setConnected: (v: boolean) => void;
      connect: () => void;
      onEvent: (event: any) => void;
    }
  ) => {
    try {
      const response = await fetch(ctx.url, {
        signal: controller.signal,
        credentials: 'include',
      });
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      if (!response.body) {
        throw new Error('No response body');
      }
      
      ctx.setConnected(true);
      ctx.retryCountRef.current = 0;
      ctx.onOpen?.();

      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        
        buffer += new TextDecoder().decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));
              ctx.onEvent(data);
            } catch {
              // 忽略解析错误
            }
          }
        }
      }
    } catch (error) {
      if (error.name === 'AbortError') return;
      ctx.onError?.(error);
      ctx.setConnected(false);
      
      if (ctx.retryCountRef.current < ctx.maxRetries) {
        ctx.retryCountRef.current++;
        setTimeout(ctx.connect, ctx.retryInterval);
      } else {
        ctx.onClose?.();
      }
    }
  };

  const connect = useCallback(async () => {
    if (!enabled) return;
    
    const controller = new AbortController();
    abortRef.current = controller;
    
    try {
      const response = await fetch(url, {
        signal: controller.signal,
        credentials: 'include',
      });
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      if (!response.body) {
        throw new Error('No response body');
      }
      
      setConnected(true);
      retryCountRef.current = 0;
      onOpen?.();

      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
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
    } catch (error) {
      if (error.name === 'AbortError') return;
      onError?.(error);
      setConnected(false);
      
      if (retryCountRef.current < maxRetries) {
        retryCountRef.current++;
        setTimeout(connect, retryInterval);
      } else {
        onClose?.();
      }
    }
  }, [url, enabled, onEvent, onError, onOpen, onClose, retryInterval, maxRetries]);

  useEffect(() => {
    if (enabled) {
      connect();
    } else {
      abortRef.current?.abort();
      setConnected(false);
    }
    return () => abortRef.current?.abort();
  }, [enabled, connect]);

  return { connected, reconnect: connect };
}

export function useDebounce<T>(value: T, delay: number): T {
  const [debouncedValue, setDebouncedValue] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedValue(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);

  return debouncedValue;
}

export function useLocalStorage<T>(key: string, initialValue: T): [T, (value: T | ((val: T) => T)) => void] {
  const [storedValue, setStoredValue] = useState<T>(() => {
    try {
      const item = window.localStorage.getItem(key);
      return item ? JSON.parse(item) : initialValue;
    } catch {
      return initialValue;
    }
  });

  const setValue = (value: T | ((val: T) => T)) => {
    try {
      const valueToStore = value instanceof Function ? value(storedValue) : value;
      setStoredValue(valueToStore);
      window.localStorage.setItem(key, JSON.stringify(valueToStore));
    } catch (error) {
      console.error('Failed to save to localStorage:', error);
    }
  };

  return [storedValue, setValue];
}

export function useClickOutside(
  ref: React.RefObject<HTMLElement>,
  handler: (event: MouseEvent | TouchEvent) => void
) {
  useEffect(() => {
    const listener = (event: MouseEvent | TouchEvent) => {
      if (!ref.current || ref.current.contains(event.target as Node)) return;
      handler(event);
    };
    document.addEventListener('mousedown', listener);
    document.addEventListener('touchstart', listener);
    return () => {
      document.removeEventListener('mousedown', listener);
      document.removeEventListener('touchstart', listener);
    };
  }, [ref, handler]);
}

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(false);
  
  useEffect(() => {
    const media = window.matchMedia(query);
    if (media.matches !== matches) setMatches(media.matches);
    const listener = () => setMatches(media.matches);
    media.addEventListener('change', listener);
    return () => media.removeEventListener('change', listener);
  }, [matches, query]);
  
  return matches;
}

export function useToast() {
  const showToast = (message: string, type: 'success' | 'error' | 'warning' | 'info' = 'info', duration = 3000) => {
    console.log(`Toast [${type}]: ${message}`);
  };
  
  return { showToast };
}

export function useCopyToClipboard() {
  const [copied, setCopied] = useState(false);
  
  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
      return true;
    } catch {
      return false;
    }
  };
  
  return { copied, copy };
}

export function useKeyPress(targetKey: string, handler: () => void) {
  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      if (event.key === targetKey) {
        handler();
      }
    };
    window.addEventListener('keydown', listener);
    return () => window.removeEventListener('keydown', listener);
  }, [targetKey, handler]);
}

export function useInterval(callback: () => void, delay: number | null) {
  const savedCallback = useRef(callback);
  
  useEffect(() => {
    savedCallback.current = callback;
  }, [callback]);
  
  useEffect(() => {
    if (delay === null) return;
    const id = setInterval(() => savedCallback.current(), delay);
    return () => clearInterval(id);
  }, [delay]);
}