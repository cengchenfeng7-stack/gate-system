import { useEffect, useRef, useState, useCallback } from 'react';
import { clsx } from 'clsx';
import { Send, Mic, Image, Paperclip, AtSign, Smile, MoreVertical, RotateCcw, X, Bot, FileText, BookOpen, Reply } from 'lucide-react';
import { useSessionStore, useReplyStore, useStreamingStore, useAssistantStore, useToastStore, useUIStore } from '@/store';
import { api, sendChat } from '@/api';
// import { MessageBubble, InputArea, AssistantLibrary, ToolEventCard, SuggestionCard } from '@/components/chat';
import { Button, Avatar, Badge, Tooltip } from '@/components/common';
import { format } from 'date-fns';

export function MainWorkbench() {
  const { 
    tabs, 
    activeTabId, 
    currentSessionId, 
    currentMode, 
    setCurrentSession, 
    currentModel,
    intelligenceLevel,
    workDir,
    setMode,
    setModel,
    setIntelligenceLevel,
    setWorkDir,
    updateTab,
    closeTab,
    sessions,
  } = useSessionStore();
  
  const { pendingReply, setPendingReply } = useReplyStore();
  const { streamingMessages } = useStreamingStore();
  const { selectedAgent, setSelectedAgent } = useAssistantStore();
  const { showToast } = useToastStore();
  const { sidebarTab, setSidebarTab } = useUIStore();
  
  const activeTab = tabs.find(t => t.id === activeTabId);
  const currentSession = sessions.find(s => s.id === currentSessionId);
  const messagesRef = useRef<HTMLDivElement>(null);
  const [messages, setMessages] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [references, setReferences] = useState<Array<{type: 'file'|'knowledge', id: string, name: string, content?: string}>>([]);
  const [pendingImages, setPendingImages] = useState<string[]>([]);

  // 加载消息
  const loadMessages = useCallback(async (sessionId: string) => {
    if (!sessionId) {
      setMessages([]);
      return;
    }
    setLoading(true);
    try {
      const { session, messages: msgs } = await api.getSession(sessionId);
      setMessages(msgs || []);
    } catch (error) {
      console.error('加载消息失败:', error);
      showToast('加载消息失败', 'error');
    } finally {
      setLoading(false);
    }
  }, []);

  // 会话切换
  useEffect(() => {
    if (activeTab?.sessionId) {
      loadMessages(activeTab.sessionId);
    } else {
      setMessages([]);
    }
  }, [activeTab?.sessionId, loadMessages]);

  // 滚动到底部
  const scrollToBottom = useCallback(() => {
    messagesRef.current?.scrollTo({ top: messagesRef.current.scrollHeight, behavior: 'smooth' });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, isGenerating, scrollToBottom]);

  // 发送消息
  const handleSend = async (content: string, options?: { images?: string[]; allowWrites?: boolean }) => {
    if (!activeTab || isGenerating) return;
    
    const sessionId = activeTab.sessionId || currentSessionId;
    if (!sessionId) {
      showToast('请先选择或创建会话', 'warning');
      return;
    }

    const userMessage = {
      id: `msg_${Date.now()}`,
      session_id: sessionId,
      role: 'user',
      content,
      images: options?.images || [],
      references: references.map(r => ({ type: r.type, id: r.id, name: r.name })),
      created_at: new Date().toISOString(),
    };

    // 乐观更新
    setMessages(prev => [...prev, userMessage]);
    setReferences([]);
    setPendingImages([]);
    setIsGenerating(true);
    updateTab(activeTabId!, { isGenerating: true });

    try {
      await sendChat({
        sessionId,
        message: content,
        mode: currentMode,
        model: currentModel || undefined,
        images: options?.images,
        replyToId: useReplyStore.getState().pendingReply?.id,
        allowWrites: options?.allowWrites,
        onEvent: (event: any) => {
          handleSSEEvent(event, sessionId);
        },
        onError: (error) => {
          showToast(`发送失败: ${error.message}`, 'error');
          setIsGenerating(false);
          updateTab(activeTabId!, { isGenerating: false });
        },
        onComplete: () => {
          setIsGenerating(false);
          updateTab(activeTabId!, { isGenerating: false });
          loadMessages(sessionId); // 重新加载确保数据一致
        },
      });
      
      setPendingReply(null);
    } catch (error) {
      showToast(`发送失败: ${(error as Error).message}`, 'error');
      setIsGenerating(false);
      updateTab(activeTabId!, { isGenerating: false });
    }
  };
  const handleSendDeps = [activeTab, activeTabId, currentMode, currentModel, currentSessionId, isGenerating, loadMessages, references, showToast, updateTab];
  // 使用 useCallback 包装 handleSend 以避免重复声明
  const handleSendCallback = useCallback(handleSend, handleSendDeps);

  // 处理 SSE 事件
  const handleSSEEvent = (event: any, sessionId: string) => {
    switch (event.type) {
      case 'text':
        // 流式文本更新
        break;
      case 'tool_start':
      case 'tool_end':
        // 工具调用事件
        break;
      case 'confirm_request':
        // 确认卡
        break;
      case 'done':
        // 完成
        break;
    }
  };

  // 处理 @ 引用
  const handleReferenceSelect = (ref: {type: 'file'|'knowledge', id: string, name: string, content?: string}) => {
    setReferences(prev => [...prev, ref]);
  };

  const handleReferenceRemove = (id: string) => {
    setReferences(prev => prev.filter(r => r.id !== id));
  };

  // 粘贴图片
  const handlePaste = (e: React.ClipboardEvent) => {
    const items = e.clipboardData.items;
    for (const item of items) {
      if (item.type.startsWith('image/')) {
        const file = item.getAsFile();
        if (file) {
          const reader = new FileReader();
          reader.onload = (e) => {
            setPendingImages(prev => [...prev, e.target?.result as string]);
          };
          reader.readAsDataURL(file);
        }
      }
    }
  };

  // 移除图片
  const removeImage = (index: number) => {
    setPendingImages(prev => prev.filter((_, i) => i !== index));
  };

  // 打开历史会话
  const handleHistoryClick = (session: any) => {
    const tabId = useSessionStore.getState().addTab({
      sessionId: session.id,
      mode: session.mode,
      model: session.model,
      workDir: 'workspace',
    });
    useSessionStore.getState().setActiveTab(tabId);
    setShowHistory(false);
  };

  return (
    <div className="flex-1 flex flex-col min-h-0 relative">
      {/* 消息列表 */}
      <div 
        ref={messagesRef}
        className="flex-1 overflow-y-auto p-4 space-y-4"
        onPaste={handlePaste}
      >
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <div className="text-center">
              <div className="animate-spin h-8 w-8 border-2 border-temple-primary border-t-transparent rounded-full mx-auto mb-2" />
              <p className="text-temple-textMuted">加载消息中...</p>
            </div>
          </div>
        ) : messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center px-8">
            <div className="h-16 w-16 rounded-full bg-temple-primary/10 flex items-center justify-center mb-4">
              <Bot className="h-8 w-8 text-temple-primary" />
            </div>
            <h3 className="text-lg font-medium text-temple-text mb-2">欢迎使用 AI 神殿</h3>
            <p className="text-temple-textMuted mb-6 max-w-md">
              选择左侧助手开始私聊，或选择群聊进行多 Agent 协作。输入 @ 可引用文件或知识库。
            </p>
            <div className="flex gap-2 flex-wrap justify-center">
              {['帮我写一个 Python 脚本', '解释这个代码的作用', '创建一个待办事项应用', '分析这段日志的错误'].map((s, i) => (
                <button
                  key={i}
                  onClick={() => handleSend(s)}
                  className="px-4 py-2 text-sm bg-temple-surface border border-temple-border rounded-lg hover:bg-temple-border transition-colors"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <>
            {messages.map((msg, index) => (
              <MessageBubble
                key={msg.id}
                message={msg}
                isOwn={msg.role === 'user'}
                streaming={streamingMessages.get(msg.id)?.content}
                onReply={() => setPendingReply({ id: msg.id, sender: msg.agent_name || msg.role, snippet: msg.content.slice(0, 100) })}
                onReferenceClick={handleReferenceSelect}
              />
            ))}
            {isGenerating && (
              <div className="flex items-start gap-3 animate-fade-in">
                <Avatar fallback="AI" size="sm" color="#6d5aff" />
                <div className="bg-temple-surface border border-temple-border rounded-2xl rounded-bl-md p-4 max-w-3xl">
                  <div className="flex items-center gap-2">
                    <div className="h-2 w-2 rounded-full bg-temple-primary animate-pulse-slow" />
                    <div className="h-2 w-2 rounded-full bg-temple-primary animate-pulse-slow" style={{animationDelay: '100ms'}} />
                    <div className="h-2 w-2 rounded-full bg-temple-primary animate-pulse-slow" style={{animationDelay: '200ms'}} />
                  </div>
                </div>
              </div>
            )}
          </>
        )}
        
        {/* 回复栏 */}
        {useReplyStore.getState().pendingReply && (
          <div className="absolute bottom-[80px] left-4 right-4 mb-4 animate-slide-up">
            <div className="bg-temple-primary/10 border border-temple-primary/30 rounded-lg p-3">
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm font-medium text-temple-primary">回复给 </span>
                <button onClick={() => setPendingReply(null)} className="text-temple-textMuted hover:text-temple-text">
                  <X className="h-4 w-4" />
                </button>
              </div>
              <p className="text-sm text-temple-textMuted truncate">
                {useReplyStore.getState().pendingReply?.sender}: {useReplyStore.getState().pendingReply?.snippet}
              </p>
            </div>
          </div>
        )}

        {/* 引用芯片 */}
        {references.length > 0 && (
          <div className="absolute bottom-[80px] left-4 right-4 mb-2 flex flex-wrap gap-2 animate-slide-up" style={{maxHeight: '120px', overflow: 'auto'}}>
            {references.map((ref, i) => (
              <span key={ref.id} className="inline-flex items-center gap-1 px-2 py-1 bg-temple-primary/10 border border-temple-primary/30 rounded-full text-sm text-temple-primary">
                {ref.type === 'file' ? <FileText className="h-3 w-3" /> : <BookOpen className="h-3 w-3" />}
                {ref.name}
                <button onClick={() => handleReferenceRemove(ref.id)} className="ml-1 hover:text-temple-danger">
                  <X className="h-3 w-3" />
                </button>
              </span>
            ))}
          </div>
        )}

        {/* 待发送图片预览 */}
        {pendingImages.length > 0 && (
          <div className="absolute bottom-[80px] left-4 right-4 mb-2 flex gap-2 animate-slide-up" style={{maxHeight: '100px', overflow: 'auto'}}>
            {pendingImages.map((img, i) => (
              <div key={i} className="relative group">
                <img src={img} alt="" className="h-20 w-20 object-cover rounded-lg" />
                <button onClick={() => removeImage(i)} className="absolute top-1 right-1 p-1 bg-black/50 text-white rounded-full hover:bg-black/70">
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>
        )}

        {/* 建议卡片 - 待实现 */}
      </div>

      {/* 输入区域 */}
      <InputArea
        value=""
        onSend={handleSend}
        onReferenceClick={() => setSidebarTab('assistants')}
        placeholder={currentSessionId ? '发送消息...' : '请先选择或创建会话'}
        disabled={isGenerating || !currentSessionId}
        pendingImages={pendingImages}
        onImageRemove={removeImage}
        references={references}
        onReferenceRemove={handleReferenceRemove}
        pendingReply={useReplyStore.getState().pendingReply}
        onReplyCancel={() => setPendingReply(null)}
      />
    </div>
  );
}

// 消息气泡组件
function MessageBubble({ message, isOwn, streaming, onReply, onReferenceClick }: any) {
  const avatarColors: Record<string, string> = {
    code: '#0052d9',
    arch: '#722ed1',
    gui: '#ed7b2f',
    ops: '#2ba471',
  };

  const formatTime = (dateStr: string) => {
    try {
      return format(new Date(dateStr), 'HH:mm');
    } catch {
      return '';
    }
  };

  const renderContent = (content: string) => {
    return content
      .replace(/```(\w+)?\n([\s\S]*?)```/g, '<pre class="bg-temple-bg p-3 rounded-lg overflow-x-auto"><code class="language-$1">$2</code></pre>')
      .replace(/`([^`]+)`/g, '<code class="bg-temple-bg px-1 py-0.5 rounded text-temple-primary">$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/\*([^*]+)\*/g, '<em>$1</em>')
      .replace(/\n/g, '<br>');
  };

  const content = streaming || message.content;
  const time = formatTime(message.created_at);
  const avatarColor = message.agent_color || avatarColors[message.agent_id || ''] || '#6d5aff';

  return (
    <div className={clsx('flex gap-3 animate-fade-in', isOwn ? 'flex-row-reverse' : '')}>
      {!isOwn && (
        <Avatar 
          fallback={message.agent_avatar || 'AI'} 
          size="sm" 
          color={avatarColor}
          title={message.agent_name || 'Assistant'}
        />
      )}
      <div className={clsx('flex-1 min-w-0', isOwn ? 'text-right' : '')}>
        {!isOwn && message.agent_name && (
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs font-medium text-temple-text">{message.agent_name}</span>
            <span className="text-xs text-temple-textMuted">{format(new Date(message.created_at), 'HH:mm')}</span>
          </div>
        )}
        <div 
          className={clsx(
            'inline-block max-w-[85%] px-4 py-2.5 rounded-2xl',
            isOwn 
              ? 'bg-temple-primary text-white rounded-br-md' 
              : 'bg-temple-surface border border-temple-border text-temple-text rounded-bl-md'
          )}
          dangerouslySetInnerHTML={{ __html: renderContent(content) }}
        />
        {message.images && message.images.length > 0 && (
          <div className="mt-2 flex gap-2">
            {message.images.map((img: string, i: number) => (
              <img key={i} src={img} alt="" className="h-32 w-32 object-cover rounded-lg" />
            ))}
          </div>
        )}
        {message.references && message.references.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">
            {message.references.map((ref: any) => (
              <button
                key={ref.id}
                onClick={onReferenceClick}
                className="inline-flex items-center gap-1 px-2 py-0.5 bg-temple-primary/10 border border-temple-primary/30 rounded-full text-xs text-temple-primary"
              >
                {ref.type === 'file' ? <FileText className="h-2.5 w-2.5" /> : <BookOpen className="h-2.5 w-2.5" />}
                {ref.name}
              </button>
            ))}
          </div>
        )}
        <div className="flex items-center gap-2 mt-1.5">
          {!isOwn && (
            <Tooltip content="回复" position="bottom">
              <button onClick={onReply} className="p-1 rounded hover:bg-temple-border transition-colors text-temple-textMuted hover:text-temple-text">
                <Reply className="h-4 w-4" />
              </button>
            </Tooltip>
          )}
          <span className="text-xs text-temple-textMuted">{format(new Date(message.created_at), 'HH:mm')}</span>
        </div>
      </div>
      {isOwn && <Avatar fallback="我" size="sm" color="#6d5aff" />}
    </div>
  );
}

import { Bot, FileText, BookOpen, Reply } from 'lucide-react';