import { useState, useEffect } from 'react';
import { clsx } from 'clsx';
import {
  LayoutDashboard,
  Users,
  BookOpen,
  Cpu,
  MessagesSquare,
  Settings,
  FileText,
  Activity,
  Database,
  Store,
  GitBranch,
  Brain,
  ChevronLeft,
  ChevronRight,
  Search,
  Plus,
  Trash2,
  Edit,
  Eye,
  Download,
  Upload,
  RefreshCw,
  Filter,
  MoreVertical,
  X,
  Bot,
  ChevronLeft as ChevronLeftIcon,
  ChevronRight as ChevronRightIcon,
  Check,
} from 'lucide-react';
import { useSessionStore, useUIStore, useToastStore, useAutonomyStore, useAssistantStore } from '@/store';
import { useAuth } from '@/hooks/useAuth';
import { Tooltip } from '../common';
import { Button, Avatar, Modal, Badge, Card, Table, Pagination, Tabs, Dropdown, Input, Select } from '../common';

const NAV_ITEMS = [
  { id: 'dashboard', label: '数据概览', icon: LayoutDashboard },
  { id: 'agents', label: '智能体管理', icon: Users },
  { id: 'faq', label: '知识库管理', icon: BookOpen },
  { id: 'models', label: '模型配置', icon: Cpu },
  { id: 'messages', label: '对话记录', icon: MessagesSquare },
  { id: 'settings', label: '系统设置', icon: Settings },
  { id: 'prompts', label: '提示词与智力', icon: FileText },
  { id: 'audit', label: '执行日志', icon: Activity },
  { id: 'store', label: 'Agent 商店', icon: Store },
  { id: 'graph', label: '知识图谱', icon: GitBranch },
  { id: 'database', label: '数据库', icon: Database },
  { id: 'autonomy', label: '自主性 L2/L3', icon: Brain },
] as const;

export function Sidebar() {
  const { sidebarOpen, sidebarTab, toggleSidebar, setSidebarTab } = useUIStore();
  const { sessions } = useSessionStore();
  const [showGroups, setShowGroups] = useState(true);
  
  return (
    <aside
      className={clsx(
        'fixed left-0 top-0 h-full bg-temple-surface border-r border-temple-border transition-all duration-300 z-40 flex flex-col',
        sidebarOpen ? 'w-72' : 'w-16'
      )}
    >
      <div className={clsx('flex items-center justify-between h-14 px-4 border-b border-temple-border', !sidebarOpen && 'justify-center')}>
        {sidebarOpen && (
          <div className="flex items-center gap-2">
            <div className="h-8 w-8 rounded-lg bg-temple-primary flex items-center justify-center">
              <Brain className="h-5 w-5 text-white" />
            </div>
            <span className="font-bold text-temple-text">AI 神殿</span>
          </div>
        )}
        <Tooltip content={sidebarOpen ? '折叠侧边栏' : '展开侧边栏'} position="right">
          <button onClick={toggleSidebar} className="p-1.5 rounded-lg hover:bg-temple-border transition-colors" aria-label={sidebarOpen ? '折叠' : '展开'}>
            {sidebarOpen ? <ChevronLeft className="h-5 w-5 text-temple-textMuted" /> : <ChevronRight className="h-5 w-5 text-temple-textMuted" />}
          </button>
        </Tooltip>
      </div>

      <nav className="flex-1 overflow-y-auto p-3 space-y-1" role="navigation" aria-label="主导航">
        {NAV_ITEMS.map((item) => (
          <Tooltip key={item.id} content={item.label} position="right" disabled={sidebarOpen}>
            <button
              onClick={() => {}}
              className={clsx(
                'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-temple-textMuted hover:text-temple-text hover:bg-temple-border transition-all duration-200',
                sidebarOpen ? '' : 'justify-center'
              )}
              aria-label={item.label}
            >
              <item.icon className="h-5 w-5 flex-shrink-0" />
              {sidebarOpen && <span className="font-medium">{item.label}</span>}
            </button>
          </Tooltip>
        ))}

        {sidebarOpen && (
          <div className="pt-4 border-t border-temple-border">
            <div className="flex items-center gap-2 px-2 mb-2">
              <button
                onClick={() => setSidebarTab('assistants')}
                className={clsx(
                  'flex-1 flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors',
                  sidebarTab === 'assistants' ? 'bg-temple-primary/10 text-temple-primary' : 'text-temple-textMuted hover:text-temple-text hover:bg-temple-border'
                )}
              >
                <Users className="h-4 w-4" />
                助手
              </button>
              <button
                onClick={() => setSidebarTab('groups')}
                className={clsx(
                  'flex-1 flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors',
                  sidebarTab === 'groups' ? 'bg-temple-primary/10 text-temple-primary' : 'text-temple-textMuted hover:text-temple-text hover:bg-temple-border'
                )}
              >
                <Users className="h-4 w-4" />
                群聊
              </button>
            </div>
            
            {sidebarTab === 'assistants' ? (
              <AssistantList />
            ) : (
              <GroupList />
            )}
          </div>
        )}
      </nav>

      {sidebarOpen && (
        <div className="p-3 border-t border-temple-border">
          <div className="flex items-center gap-3">
            <Avatar fallback="U" size="md" color="#6d5aff" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-temple-text truncate">用户</p>
              <p className="text-xs text-temple-textMuted">本地模式</p>
            </div>
          </div>
        </div>
      )}
    </aside>
  );
}

function AssistantList() {
  const { sessions, currentSessionId, setCurrentSession, addTab } = useSessionStore();
  const { selectedAgent, setSelectedAgent } = useAssistantStore();
  const [search, setSearch] = useState('');
  const { showToast } = useToastStore();

  const assistants = [
    { id: 'code', name: '全栈开发 · 阿码', role: '全栈编程、代码重构与文件落地', avatar: 'Code', color: '#0052d9' },
    { id: 'arch', name: '架构规划 · 小策', role: '任务分解、系统架构与方案设计', avatar: 'Sparkles', color: '#722ed1' },
    { id: 'gui', name: '桌面操控 · 小控', role: 'Windows UI 自动化与截屏视觉感知', avatar: 'Desktop', color: '#ed7b2f' },
    { id: 'ops', name: '系统排查 · 阿测', role: '命令执行、环境诊断与故障排查', avatar: 'Terminal', color: '#2ba471' },
  ];

  const filtered = assistants.filter(a => 
    a.name.toLowerCase().includes(search.toLowerCase()) || 
    a.role.toLowerCase().includes(search.toLowerCase())
  );

  const handleNewChat = (agent: typeof assistants[0]) => {
    setSelectedAgent(agent);
    const tabId = useSessionStore.getState().addTab({
      sessionId: null,
      mode: 'private',
      model: '',
      workDir: 'workspace',
    });
    useSessionStore.getState().setActiveTab(tabId);
  };

  return (
    <div className="flex-1 overflow-y-auto min-h-0">
      <div className="p-2 mb-2">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-temple-textMuted" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索助手..."
            className="input input-sm pl-9"
          />
        </div>
      </div>
      <div className="px-2 space-y-1">
        {filtered.map((assistant) => (
          <button
            key={assistant.id}
            onClick={() => handleNewChat(assistant)}
            className={clsx(
              'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-left transition-colors',
              'hover:bg-temple-border text-temple-text'
            )}
          >
            <Avatar fallback={assistant.avatar} size="sm" color={assistant.color} />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-temple-text truncate">{assistant.name}</p>
              <p className="text-xs text-temple-textMuted truncate">{assistant.role}</p>
            </div>
          </button>
        ))}
        <div className="p-2 pt-0">
          <button
            onClick={() => { /* 创建新助手 */ }}
            className={clsx('w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm font-medium text-temple-textMuted hover:text-temple-text hover:bg-temple-border transition-colors')}
          >
            <Plus className="h-4 w-4" />
            <span>新建助手</span>
          </button>
        </div>
      </div>
    </div>
  );
}

function GroupList() {
  const { sessions } = useSessionStore();
  const { currentSessionId, setCurrentSession } = useSessionStore();
  const [search, setSearch] = useState('');
  const [showFolded, setShowFolded] = useState(false);

  const groupSessions = sessions.filter(s => s.mode === 'group' || !s.mode);
  const pinned = groupSessions.filter(s => s.pinned).sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
  const normal = groupSessions.filter(s => !s.pinned).sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
  const folded = groupSessions.filter(s => s.folded);

  const filtered = [...pinned, ...normal].filter(s => 
    s.title.toLowerCase().includes(search.toLowerCase()) ||
    s.group_no?.includes(search)
  );

  return (
    <div className="flex-1 overflow-y-auto min-h-0">
      <div className="p-2 mb-2">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-temple-textMuted" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索群聊..."
            className="input input-sm pl-9"
          />
        </div>
      </div>

      {pinned.length > 0 && (
        <div className="px-2 mb-2">
          <p className="px-2 py-1 text-xs font-medium text-temple-textMuted uppercase tracking-wider">置顶群聊</p>
          <GroupItems groups={pinned.filter(s => filtered.includes(s))} />
        </div>
      )}

      <div className="px-2 mb-2">
        <p className="px-2 py-1 text-xs font-medium text-temple-textMuted uppercase tracking-wider">群聊列表</p>
        <GroupItems groups={normal.filter(s => filtered.includes(s))} />
      </div>

      {folded.length > 0 && (
        <div className="px-2 mb-2">
          <button
            onClick={() => setShowFolded(!showFolded)}
            className="w-full flex items-center justify-between px-2 py-1 text-xs font-medium text-temple-textMuted hover:text-temple-text"
          >
            <span>折叠的群聊 ({folded.length})</span>
            <ChevronRight className={clsx('h-4 w-4 transition-transform', showFolded && 'rotate-90')} />
          </button>
          {showFolded && (
            <GroupItems groups={folded.filter(s => filtered.includes(s))} />
          )}
        </div>
      )}

      <div className="p-2 pt-0">
        <button className={clsx('w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm font-medium text-temple-textMuted hover:text-temple-text hover:bg-temple-border transition-colors')}>
          <Plus className="h-4 w-4" />
          <span>新建群聊</span>
        </button>
      </div>
    </div>
  );
}

function GroupItems({ groups }: { groups: any[] }) {
  const { currentSessionId, setCurrentSession, addTab } = useSessionStore();
  
  return (
    <div className="space-y-1">
      {groups.map((group) => (
        <button
          key={group.id}
          onClick={() => {
            setCurrentSession(group.id);
            const tabId = useSessionStore.getState().addTab({
              sessionId: group.id,
              mode: 'group',
              model: group.model,
              workDir: 'workspace',
            });
            useSessionStore.getState().setActiveTab(tabId);
          }}
          className={clsx(
            'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-left transition-colors',
            'hover:bg-temple-border'
          )}
        >
          <div className={clsx('h-8 w-8 rounded-lg flex items-center justify-center flex-shrink-0', group.pinned && 'ring-2 ring-temple-primary/50')}>
            <span className="text-lg">{group.avatar || '👥'}</span>
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <p className="text-sm font-medium text-temple-text truncate">{group.title || '未命名'}</p>
              {group.group_no && <Badge variant="neutral" size="sm">{group.group_no}</Badge>}
              {group.pinned && <span className="h-1.5 w-1.5 rounded-full bg-temple-primary" title="置顶" />}
              {group.muted && <span className="h-1.5 w-1.5 rounded-full bg-temple-textMuted" title="免打扰" />}
            </div>
            <p className="text-xs text-temple-textMuted truncate flex items-center gap-1">
              <span>{group.memberCount || 0} 人</span>
              {group.lastMessage && <span>· {group.lastMessage.slice(0, 30)}</span>}
            </p>
          </div>
          {group.unreadCount > 0 && (
            <Badge variant="primary" size="sm">{group.unreadCount > 99 ? '99+' : group.unreadCount}</Badge>
          )}
        </button>
      ))}
    </div>
  );
}

export function Header() {
  const { currentMode, currentModel, intelligenceLevel, workDir, setMode, setModel, setIntelligenceLevel, setWorkDir } = useSessionStore();
  const { settingsOpen, showSettingsTab, closeSettings } = useUIStore();
  const { sessions } = useSessionStore();
  const { showToast } = useToastStore();
  const [search, setSearch] = useState('');
  
  const modes = [
    { id: 'group', label: '👥 群聊', desc: '多 Agent 协作讨论' },
    { id: 'private', label: '💬 私聊', desc: '1v1 单 Agent 对话' },
    { id: 'workflow', label: '⚡ 工作流', desc: '分析→执行→总结' },
    { id: 'proactive', label: '🎯 主动服务', desc: '规则分析+建议' },
    { id: 'continuous', label: '🔄 持续对话', desc: '读取任务真实执行' },
    { id: 'creative', label: '🎨 创作协作', desc: '四轮真实创作' },
    { id: 'analysis', label: '📊 数据分析', desc: '收集→分析→报告' },
    { id: 'autonomous', label: '🤖 自主代理', desc: '自规划→执行→进化' },
    { id: 'cross_platform', label: '🌐 跨平台', desc: '多平台内容适配' },
  ];

  return (
    <header className="sticky top-0 z-30 h-14 bg-temple-surface/95 backdrop-blur-sm border-b border-temple-border flex items-center gap-4 px-4">
      <div className="flex items-center gap-2">
        <select
          value={currentMode}
          onChange={(e) => setMode(e.target.value as any)}
          className="input input-sm w-auto min-w-[140px] bg-temple-bg"
          aria-label="会话模式"
        >
          {modes.map((m) => (
            <option key={m.id} value={m.id} title={m.desc}>{m.label}</option>
          ))}
        </select>
      </div>

      <div className="flex items-center gap-2 ml-4">
        <select
          value={currentModel}
          onChange={(e) => setModel(e.target.value)}
          className="input input-sm w-auto min-w-[180px] bg-temple-bg"
          aria-label="AI 模型"
        >
          <option value="">选择模型...</option>
        </select>
      </div>

      <div className="flex items-center gap-2 ml-2">
        <select
          value={intelligenceLevel}
          onChange={(e) => setIntelligenceLevel(Number(e.target.value))}
          className="input input-sm w-auto min-w-[100px] bg-temple-bg"
          aria-label="智力档位"
        >
          {[1,2,3,4,5,6].map((l) => (
            <option key={l} value={l}>Lv.{l}</option>
          ))}
        </select>
      </div>

      <div className="flex items-center gap-2 ml-2">
        <select
          value={workDir}
          onChange={(e) => setWorkDir(e.target.value)}
          className="input input-sm w-auto min-w-[120px] bg-temple-bg"
          aria-label="工作目录"
        >
          <option value="workspace">workspace</option>
          <option value="project">项目根目录</option>
        </select>
      </div>

      <div className="flex-1" />

      <div className="relative w-64">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-temple-textMuted" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="搜索消息..."
          className="input input-sm pl-9 pr-9 w-full"
        />
      </div>

      <div className="flex items-center gap-2 ml-4">
        <Tooltip content="设置中心" position="bottom">
          <button onClick={() => showSettingsTab()} className="p-2 rounded-lg hover:bg-temple-border transition-colors" aria-label="设置中心">
            <Settings className="h-5 w-5 text-temple-textMuted" />
          </button>
        </Tooltip>
        <Tooltip content="用量中心" position="bottom">
          <button className="p-2 rounded-lg hover:bg-temple-border transition-colors" aria-label="用量中心">
            <Activity className="h-5 w-5 text-temple-textMuted" />
          </button>
        </Tooltip>
        <Tooltip content="工具页" position="bottom">
          <button className="p-2 rounded-lg hover:bg-temple-border transition-colors" aria-label="工具页">
            <ArrowRightLeft className="h-5 w-5 text-temple-textMuted" />
          </button>
        </Tooltip>
        <Tooltip content="刷新模型" position="bottom">
          <button className="p-2 rounded-lg hover:bg-temple-border transition-colors" aria-label="刷新模型">
            <ArrowRightLeft className="h-5 w-5 text-temple-textMuted" />
          </button>
        </Tooltip>
      </div>
    </header>
  );
}

export function TabBar() {
  const { tabs, activeTabId, setActiveTab, closeTab, updateTab } = useSessionStore();
  const { sessions } = useSessionStore();
  const [dragState, setDragState] = useState<{ dragging: string | null; over: string | null }>({ dragging: null, over: null });

  if (tabs.length === 0) {
    return (
      <div className="h-10 bg-temple-surface border-b border-temple-border flex items-center justify-center">
        <button
          onClick={() => {
            useSessionStore.getState().addTab({ sessionId: null, mode: 'group', model: '', workDir: 'workspace' });
          }}
          className="p-2 rounded-lg hover:bg-temple-border transition-colors text-temple-textMuted"
          aria-label="新建标签"
        >
          <Plus className="h-5 w-5" />
        </button>
      </div>
    );
  }

  return (
    <div className="h-10 bg-temple-surface border-b border-temple-border flex items-center overflow-x-auto px-2 gap-1" role="tablist">
      {tabs.map((tab) => {
        const session = sessions.find(s => s.id === tab.sessionId);
        const isActive = activeTabId === tab.id;
        
        return (
          <button
            key={tab.id}
            role="tab"
            aria-selected={isActive}
            aria-controls={`panel-${tab.id}`}
            id={`tab-${tab.id}`}
            onClick={() => useSessionStore.getState().setActiveTab(tab.id)}
            onDoubleClick={() => { /* 重命名 */ }}
            draggable
            onDragStart={(e) => setDragState({ dragging: tab.id, over: null })}
            onDragOver={(e) => { e.preventDefault(); setDragState(d => ({ ...d, over: tab.id })); }}
            onDragEnd={() => setDragState({ dragging: null, over: null })}
            className={clsx(
              'relative flex items-center gap-2 px-3 py-1.5 rounded-l-lg rounded-r-lg min-w-0 transition-all',
              isActive ? 'bg-temple-bg text-temple-text' : 'bg-temple-surface text-temple-textMuted hover:text-temple-text',
              dragState.dragging === tab.id && 'opacity-50',
              dragState.over === tab.id && 'ring-2 ring-temple-primary ring-inset'
            )}
            style={{ minWidth: '120px', maxWidth: '200px' }}
          >
            <span className="truncate flex-1 flex items-center gap-1.5">
              {tab.mode !== 'group' && <span className="text-xs px-1.5 py-0.5 rounded bg-temple-primary/20 text-temple-primary/80">{tab.mode.slice(0, 3)}</span>}
              <span className="truncate">{session?.title || tab.sessionId ? '会话' : '新对话'}</span>
              {tab.isGenerating && <span className="h-1.5 w-1.5 rounded-full bg-temple-primary animate-pulse-slow" />}
            </span>
            <button
              onClick={(e) => { e.stopPropagation(); closeTab(tab.id); }}
              className={clsx('p-1 rounded hover:bg-temple-border/50 transition-colors opacity-0 group-hover:opacity-100', isActive ? 'opacity-100' : '')}
              aria-label="关闭标签"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </button>
        );
      })}
      <button
        onClick={() => {
          useSessionStore.getState().addTab({ sessionId: null, mode: 'group', model: '', workDir: 'workspace' });
        }}
        className="p-2 rounded-lg hover:bg-temple-border transition-colors text-temple-textMuted"
        aria-label="新建标签"
      >
        <Plus className="h-4 w-4" />
      </button>
    </div>
  );
}

export function SettingsDrawer() {
  const { settingsOpen, closeSettings } = useUIStore();
  const [activeTab, setActiveTab] = useState<'models' | 'tools' | 'prefs' | 'usage' | 'open' | 'about'>('models');
  
  if (!settingsOpen) return null;

  return (
    <Modal open={settingsOpen} onClose={closeSettings} title="设置中心" size="xl">
      <Tabs
        tabs={[
          { id: 'models', label: '模型服务', icon: <Cpu className="h-4 w-4" /> },
          { id: 'tools', label: '工具', icon: <Settings className="h-4 w-4" /> },
          { id: 'prefs', label: '偏好', icon: <FileText className="h-4 w-4" /> },
          { id: 'usage', label: '用量', icon: <Activity className="h-4 w-4" /> },
          { id: 'open', label: '开放', icon: <GitBranch className="h-4 w-4" /> },
          { id: 'about', label: '关于', icon: <HelpCircle className="h-4 w-4" /> },
        ]}
        activeTab={activeTab}
        onChange={setActiveTab}
      />
      <div id="panel-models" role="tabpanel" className="mt-4">
        <ModelsSettings />
      </div>
      <div id="panel-tools" role="tabpanel" className="mt-4 hidden">
        <ToolsSettings />
      </div>
      <div id="panel-prefs" role="tabpanel" className="mt-4 hidden">
        <PrefsSettings />
      </div>
      <div id="panel-usage" role="tabpanel" className="mt-4 hidden">
        <UsageSettings />
      </div>
      <div id="panel-open" role="tabpanel" className="mt-4 hidden">
        <OpenApiSettings />
      </div>
      <div id="panel-about" role="tabpanel" className="mt-4 hidden">
        <AboutSettings />
      </div>
    </Modal>
  );
}

function ModelsSettings() {
  return <div className="space-y-4 p-2">模型服务设置 - 待实现</div>;
}

function ToolsSettings() {
  return <div className="space-y-4 p-2">工具设置 - 待实现</div>;
}

function PrefsSettings() {
  return <div className="space-y-4 p-2">偏好设置 - 待实现</div>;
}

function UsageSettings() {
  return <div className="space-y-4 p-2">用量中心 - 待实现</div>;
}

function OpenApiSettings() {
  return <div className="space-y-4 p-2">开放接口 - 待实现</div>;
}

function AboutSettings() {
  return <div className="space-y-4 p-2">关于 - 待实现</div>;
}

function Tabs({ tabs, activeTab, onChange, className }: { 
  tabs: Array<{ id: string; label: string; icon?: React.ReactNode }>;
  activeTab: string;
  onChange: (id: string) => void;
  className?: string;
}) {
  return (
    <div className={clsx('flex gap-1 bg-temple-surface rounded-lg p-1', className)} role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          aria-selected={activeTab === tab.id}
          onClick={() => onChange(tab.id)}
          className={clsx(
            'tab px-3 py-2 rounded-md text-sm font-medium',
            activeTab === tab.id ? 'tab-active' : ''
          )}
        >
          {tab.icon && <span className="inline-flex items-center gap-1.5">{tab.icon}</span>}
          {tab.label}
        </button>
      ))}
    </div>
  );
}

export function AdminPanel() {
  return <div className="p-6">管理后台 - 待实现完整功能</div>;
}