import { useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Toaster } from '@/components/common';
import { AdminPanel, Sidebar, Header, TabBar, SettingsDrawer } from '@/components/layout';
import { MainWorkbench } from '@/pages/MainWorkbench';
import { useSessionStore, useToastStore } from '@/store';
import { useAuth } from '@/hooks/useAuth';

function App() {
  const { isAuthenticated, checkAuth } = useAuth();
  const { toasts, removeToast } = useToastStore();
  const { addTab, tabs, restoreTabs } = useSessionStore();

  // 初始化：恢复标签页、检查认证
  useEffect(() => {
    restoreTabs();
    checkAuth();
    
    // 如果没有标签页，创建一个默认标签
    if (tabs.length === 0) {
      addTab({ sessionId: null, mode: 'group', model: '', workDir: 'workspace' });
    }
  }, []);

  return (
    <BrowserRouter>
      <div className="min-h-screen bg-temple-bg text-temple-text">
        {/* Toast 容器 */}
        <Toaster toasts={toasts} onClose={removeToast} />
        
        {/* 设置抽屉 */}
        <SettingsDrawer />
        
        {/* 主布局 */}
        <div className="flex min-h-screen">
          {/* 侧边栏 - 仅主工作台显示 */}
          <Sidebar />
          
          {/* 主内容区 */}
          <div className="flex-1 flex flex-col min-w-0">
            <Header />
            <TabBar />
            
            <main className="flex-1 overflow-hidden">
              <Routes>
                <Route path="/admin/*" element={<AdminPanel />} />
                <Route path="/" element={<MainWorkbench />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </main>
          </div>
        </div>
      </div>
    </BrowserRouter>
  );
}

export default App;