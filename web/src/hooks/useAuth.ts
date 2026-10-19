import { useState, useEffect, useCallback } from 'react';
import { api } from '../api';

interface User {
  id: string;
  name: string;
  email?: string;
}

export function useAuth() {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const checkAuth = useCallback(async () => {
    try {
      const res = await api.get('/api/admin/auth');
      if (res.authenticated) {
        setIsAuthenticated(true);
        setUser({ id: 'admin', name: 'Administrator' });
      } else {
        setIsAuthenticated(false);
        setUser(null);
      }
    } catch {
      setIsAuthenticated(false);
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  const login = async (password: string) => {
    try {
      await api.post('/api/admin/login', { password });
      setIsAuthenticated(true);
      setUser({ id: 'admin', name: 'Administrator' });
      return true;
    } catch {
      return false;
    }
  };

  const logout = async () => {
    try {
      await api.post('/api/admin/logout');
    } catch {}
    setIsAuthenticated(false);
    setUser(null);
  };

  return { isAuthenticated, user, loading, checkAuth, login, logout };
}