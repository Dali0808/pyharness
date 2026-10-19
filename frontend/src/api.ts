import { useEffect, useState } from 'react';

export type Settings = {
  provider_id: string; model_id: string; base_url: string; api_key_env: string;
  max_steps: number; context_window: number | null; multi_agent: boolean; session: string;
};
export type Tool = { id: string; name: string; arguments: string; status: string; output: string; detail?: string };
export type Approval = { id: string; run_id: string; kind: 'edit' | 'command'; detail: string; path?: string };
export type Workspace = {
  revision: number; trusted: boolean; workspace: string; settings: Settings; api_key_set: boolean; busy: boolean;
  sessions: { id: string; title: string; path: string }[];
  active: { id: string; path: string; title: string; phase: string; messages: { role: string; text: string }[];
    tools: Tool[]; live: string; failure: string | null; notices: string; steps: number;
    usage: Record<string, number>; run_id: string | null; approval: Approval | null };
};

function connectionToken(): string {
  const supplied = new URLSearchParams(location.hash.slice(1)).get('token');
  if (supplied) {
    sessionStorage.setItem('lario-token', supplied);
    history.replaceState(null, '', location.pathname + location.search);
  }
  return sessionStorage.getItem('lario-token') ?? '';
}

export function useWorkspace() {
  const [token] = useState(connectionToken);
  const [state, setState] = useState<Workspace | null>(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState('');
  const [connectionError, setConnectionError] = useState('');
  const [pending, setPending] = useState(false);
  const accept = (next: Workspace) => setState(previous => !previous || next.revision >= previous.revision ? next : previous);

  useEffect(() => {
    const controller = new AbortController();
    let retry: ReturnType<typeof setTimeout>;
    async function connect() {
      try {
        const response = await fetch('/api/events', { headers: { Authorization: `Bearer ${token}` }, signal: controller.signal });
        if (!response.ok || !response.body) {
          if (response.status === 401) throw new Error('连接未授权。请打开启动终端中带连接令牌的完整地址。');
          throw new Error('无法连接 Python 服务，请从 lario-web 提供的地址打开页面。');
        }
        setConnected(true);
        setConnectionError('');
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (!controller.signal.aborted) {
          const { done, value } = await reader.read();
          if (done) throw new Error('连接已断开，正在重连；超过 20 秒未连接时任务将自动停止。');
          buffer += decoder.decode(value, { stream: true });
          let end: number;
          while ((end = buffer.indexOf('\n\n')) !== -1) {
            const frame = buffer.slice(0, end);
            buffer = buffer.slice(end + 2);
            if (frame.startsWith('data: ')) accept(JSON.parse(frame.slice(6)) as Workspace);
          }
        }
      } catch (reason) {
        if (controller.signal.aborted) return;
        setConnected(false);
        setConnectionError(reason instanceof Error ? reason.message : '连接失败');
        retry = setTimeout(connect, 2000);
      }
    }
    void connect();
    return () => { controller.abort(); clearTimeout(retry); };
  }, [token]);

  async function action(path: string, body: object = {}): Promise<boolean> {
    if (pending) return false;
    setPending(true);
    setError('');
    try {
      const response = await fetch(`/api/${path}`, { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '配置或输入无效，请检查后重试。');
      accept(data as Workspace);
      return true;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '请求失败，操作不会自动重试，请检查当前任务状态。');
      return false;
    } finally { setPending(false); }
  }

  return { state, connected, pending, error: connectionError || error, clearError: () => { setError(''); setConnectionError(''); }, action };
}
