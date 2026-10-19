import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import type { Session, SessionMode, Message, Agent } from '../types/api';

interface SessionState {
  sessions: Session[];
  currentSessionId: string | null;
  currentMode: SessionMode;
  currentModel: string;
  intelligenceLevel: number;
  workDir: string;
  tabs: TabState[];
  activeTabId: string | null;
  
  setSessions: (sessions: Session[]) => void;
  addSession: (session: Session) => void;
  updateSession: (id: string, data: Partial<Session>) => void;
  removeSession: (id: string) => void;
  setCurrentSession: (id: string | null) => void;
  setMode: (mode: SessionMode) => void;
  setModel: (model: string) => void;
  setIntelligenceLevel: (level: number) => void;
  setWorkDir: (dir: string) => void;
  
  addTab: (tab: Omit<TabState, 'id'>) => string;
  updateTab: (id: string, data: Partial<TabState>) => void;
  closeTab: (id: string) => void;
  setActiveTab: (id: string) => void;
  restoreTabs: (tabs: TabState[]) => void;
}

interface TabState {
  id: string;
  sessionId: string | null;
  mode: SessionMode;
  model: string;
  workDir: string;
  draft: string;
  references: ReferenceItem[];
  pendingImages: string[];
  isGenerating: boolean;
  createdAt: number;
}

interface ReferenceItem {
  type: 'file' | 'knowledge';
  id: string;
  name: string;
  content?: string;
}

const DEFAULT_TAB: Omit<TabState, 'id'> = {
  sessionId: null,
  mode: 'group',
  model: '',
  workDir: 'workspace',
  draft: '',
  references: [],
  pendingImages: [],
  isGenerating: false,
  createdAt: Date.now(),
};

export const useSessionStore = create<SessionState>()(
  persist(
    (set, get) => ({
      sessions: [],
      currentSessionId: null,
      currentMode: 'group',
      currentModel: '',
      intelligenceLevel: 3,
      workDir: 'workspace',
      tabs: [],
      activeTabId: null,
      
      setSessions: (sessions) => set({ sessions }),
      addSession: (session) => set((state) => ({ sessions: [session, ...state.sessions] })),
      updateSession: (id, data) => set((state) => ({
        sessions: state.sessions.map((s) => s.id === id ? { ...s, ...data } : s),
      })),
      removeSession: (id) => set((state) => ({
        sessions: state.sessions.filter((s) => s.id !== id),
        currentSessionId: state.currentSessionId === id ? null : state.currentSessionId,
        tabs: state.tabs.filter((t) => t.sessionId !== id),
      })),
      setCurrentSession: (id) => set({ currentSessionId: id }),
      setMode: (mode) => set({ currentMode: mode }),
      setModel: (model) => set({ currentModel: model }),
      setIntelligenceLevel: (level) => set({ intelligenceLevel: level }),
      setWorkDir: (dir) => set({ workDir: dir }),
      
      addTab: (tabData) => {
        const id = `tab_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
        const tab: TabState = { ...DEFAULT_TAB, ...tabData, id, createdAt: Date.now() };
        set((state) => ({
          tabs: [...state.tabs, tab],
          activeTabId: id,
        }));
        return id;
      },
      updateTab: (id, data) => set((state) => ({
        tabs: state.tabs.map((t) => t.id === id ? { ...t, ...data } : t),
      })),
      closeTab: (id) => set((state) => {
        const tabs = state.tabs.filter((t) => t.id !== id);
        let activeTabId = state.activeTabId;
        if (activeTabId === id) {
          activeTabId = tabs.length > 0 ? tabs[tabs.length - 1].id : null;
        }
        return { tabs, activeTabId };
      }),
      setActiveTab: (id) => set({ activeTabId: id }),
      restoreTabs: (tabs) => set({ tabs, activeTabId: tabs.length > 0 ? tabs[tabs.length - 1].id : null }),
    }),
    {
      name: 'session-store',
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        currentMode: state.currentMode,
        currentModel: state.currentModel,
        intelligenceLevel: state.intelligenceLevel,
        workDir: state.workDir,
        tabs: state.tabs,
        activeTabId: state.activeTabId,
      }),
    }
  )
);

interface AssistantStore {
  selectedAgent: Agent | null;
  setSelectedAgent: (agent: Agent | null) => void;
}

export const useAssistantStore = create<AssistantStore>((set) => ({
  selectedAgent: null,
  setSelectedAgent: (agent) => set({ selectedAgent: agent }),
}));

interface ReplyStore {
  pendingReply: { id: string; sender: string; snippet: string } | null;
  setPendingReply: (reply: { id: string; sender: string; snippet: string } | null) => void;
}

export const useReplyStore = create<ReplyStore>((set) => ({
  pendingReply: null,
  setPendingReply: (reply) => set({ pendingReply: reply }),
}));

interface StreamingStore {
  streamingMessages: Map<string, { content: string; done: boolean }>;
  setStreamingContent: (messageId: string, content: string, done?: boolean) => void;
  clearStreaming: (messageId: string) => void;
}

export const useStreamingStore = create<StreamingStore>((set) => ({
  streamingMessages: new Map(),
  setStreamingContent: (messageId, content, done = false) => set((state) => {
    const map = new Map(state.streamingMessages);
    map.set(messageId, { content, done });
    return { streamingMessages: map };
  }),
  clearStreaming: (messageId) => set((state) => {
    const map = new Map(state.streamingMessages);
    map.delete(messageId);
    return { streamingMessages: map };
  }),
}));

interface Toast {
  id: string;
  type: 'success' | 'error' | 'warning' | 'info';
  message: string;
  duration?: number;
}

interface ToastStore {
  toasts: Toast[];
  addToast: (toast: Omit<Toast, 'id'>) => void;
  removeToast: (id: string) => void;
}

export const useToastStore = create<ToastStore>((set) => ({
  toasts: [],
  addToast: (toast) => set((state) => ({
    toasts: [...state.toasts, { ...toast, id: `toast_${Date.now()}_${Math.random().toString(36).slice(2, 8)}` }],
  })),
  removeToast: (id) => set((state) => ({
    toasts: state.toasts.filter((t) => t.id !== id),
  })),
}));

interface UIStore {
  settingsOpen: boolean;
  sidebarOpen: boolean;
  sidebarTab: 'assistants' | 'groups' | 'history';
  showSettingsTab: (tab?: string) => void;
  closeSettings: () => void;
  toggleSidebar: () => void;
  setSidebarTab: (tab: 'assistants' | 'groups' | 'history') => void;
}

export const useUIStore = create<UIStore>((set) => ({
  settingsOpen: false,
  sidebarOpen: true,
  sidebarTab: 'assistants',
  showSettingsTab: (tab) => set({ settingsOpen: true }),
  closeSettings: () => set({ settingsOpen: false }),
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
  setSidebarTab: (tab) => set({ sidebarTab: tab, sidebarOpen: true }),
}));

// 自主性相关状态
interface AutonomyStore {
  patrolStatus: any;
  queueStatus: any;
  profile: any;
  strategies: any[];
  goals: any[];
  suggestions: any[];
  loading: boolean;
  
  setPatrolStatus: (status: any) => void;
  setQueueStatus: (status: any) => void;
  setProfile: (profile: any) => void;
  setStrategies: (strategies: any[]) => void;
  setGoals: (goals: any[]) => void;
  setSuggestions: (suggestions: any[]) => void;
  setLoading: (loading: boolean) => void;
}

export const useAutonomyStore = create<AutonomyStore>((set) => ({
  patrolStatus: null,
  queueStatus: null,
  profile: null,
  strategies: [],
  goals: [],
  suggestions: [],
  loading: false,
  setPatrolStatus: (status) => set({ patrolStatus: status }),
  setQueueStatus: (status) => set({ queueStatus: status }),
  setProfile: (profile) => set({ profile }),
  setStrategies: (strategies) => set({ strategies }),
  setGoals: (goals) => set({ goals }),
  setSuggestions: (suggestions) => set({ suggestions }),
  setLoading: (loading) => set({ loading }),
}));

// 设置相关状态
interface SettingsStore {
  providers: any[];
  activeProvider: any;
  models: any[];
  toolSettings: any;
  preferences: any;
  pricing: any;
  modelRoutes: any;
  familyStatus: any;
  loading: boolean;
  
  setProviders: (providers: any[]) => void;
  setActiveProvider: (provider: any) => void;
  setModels: (models: any[]) => void;
  setToolSettings: (settings: any) => void;
  setPreferences: (prefs: any) => void;
  setPricing: (pricing: any) => void;
  setModelRoutes: (routes: any) => void;
  setFamilyStatus: (status: any) => void;
  setLoading: (loading: boolean) => void;
}

export const useSettingsStore = create<SettingsStore>((set) => ({
  providers: [],
  activeProvider: null,
  models: [],
  toolSettings: null,
  preferences: null,
  pricing: null,
  modelRoutes: null,
  familyStatus: null,
  loading: false,
  setProviders: (providers) => set({ providers }),
  setActiveProvider: (provider) => set({ activeProvider: provider }),
  setModels: (models) => set({ models }),
  setToolSettings: (settings) => set({ toolSettings: settings }),
  setPreferences: (prefs) => set({ preferences: prefs }),
  setPricing: (pricing) => set({ pricing }),
  setModelRoutes: (routes) => set({ modelRoutes: routes }),
  setFamilyStatus: (status) => set({ familyStatus: status }),
  setLoading: (loading) => set({ loading }),
}));

export const useStore = {
  session: useSessionStore,
  assistant: useAssistantStore,
  reply: useReplyStore,
  streaming: useStreamingStore,
  toast: useToastStore,
  ui: useUIStore,
  autonomy: useAutonomyStore,
  settings: useSettingsStore,
};