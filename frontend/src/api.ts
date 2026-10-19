import { useEffect, useState } from 'react';

export type StoredFile = { name: string; size: number };
export type Workspace = {
  revision: number; trusted: boolean; workspace: string; settings: { model_id: string }; busy: boolean;
  sessions: { id: string; title: string; path: string }[];
  files: { documents: StoredFile[]; artifacts: StoredFile[]; bytes: number; limits: { max_file_bytes: number; max_storage_bytes: number; max_files: number } };
  account?: { username: string; usage: { day: string; calls: number; charged_tokens: number; actual_tokens: number }; limits: { daily_requests: number; daily_tokens: number } };
  active: { id: string; path: string; title: string; phase: string; messages: { role: string; text: string }[];
    tools: { id: string; name: string; arguments: string; status: string; output: string }[];
    live: string; failure: string | null; notices: string; steps: number; usage: Record<string, number> };
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
  const [needsLogin, setNeedsLogin] = useState(false);
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const [generation, setGeneration] = useState(0);
  const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
  const accept = (next: Workspace) => setState(previous => !previous || next.revision >= previous.revision ? next : previous);

  useEffect(() => {
    const controller = new AbortController();
    let retry: ReturnType<typeof setTimeout>;
    async function connect() {
      try {
        const response = await fetch('/api/events', { headers: token ? { Authorization: `Bearer ${token}` } : {}, signal: controller.signal });
        if (response.status === 401) {
          const mode = await fetch('/api/auth/mode', { headers: token ? { Authorization: `Bearer ${token}` } : {}, signal: controller.signal });
          if (mode.ok && (await mode.json()).mode === 'login') {
            setState(null); setNeedsLogin(true); setConnected(false); return;
          }
          throw new Error('请打开启动终端中带连接令牌的完整地址。');
        }
        if (!response.ok || !response.body) throw new Error('无法连接服务，请稍后重试。');
        setConnected(true); setNeedsLogin(false); setError('');
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (!controller.signal.aborted) {
          const { done, value } = await reader.read();
          if (done) throw new Error('连接已断开，正在重连；超过 20 秒未连接时任务将停止。');
          buffer += decoder.decode(value, { stream: true });
          let end: number;
          while ((end = buffer.indexOf('\n\n')) !== -1) {
            const frame = buffer.slice(0, end);
            buffer = buffer.slice(end + 2);
            if (frame.startsWith('data: ') && !controller.signal.aborted) accept(JSON.parse(frame.slice(6)) as Workspace);
          }
        }
      } catch (reason) {
        if (controller.signal.aborted) return;
        setConnected(false); setError(reason instanceof Error ? reason.message : '连接失败');
        retry = setTimeout(connect, 2000);
      }
    }
    void connect();
    return () => { controller.abort(); clearTimeout(retry); };
  }, [token, generation]);

  async function request(path: string, body: object | File = {}, method = 'POST'): Promise<boolean> {
    if (pending) return false;
    setPending(true); setError('');
    try {
      const response = await fetch(`/api/${path}`, {
        method, headers: { ...headers, 'Content-Type': body instanceof File ? 'application/octet-stream' : 'application/json' },
        body: body instanceof File ? body : JSON.stringify(body),
      });
      const data = await response.json();
      if (!response.ok) {
        if (response.status === 401) { setState(null); setConnected(false); setNeedsLogin(true); setGeneration(value => value + 1); }
        throw new Error(typeof data.detail === 'string' ? data.detail : '输入无效，请检查后重试。');
      }
      if ('active' in data) accept(data as Workspace);
      if (path === 'auth/login' || path === 'auth/logout') {
        setState(null); setConnected(false); setNeedsLogin(path === 'auth/logout'); setGeneration(value => value + 1);
      }
      return true;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '请求失败，请检查状态后再操作。');
      return false;
    } finally { setPending(false); }
  }

  async function download(kind: string, name: string) {
    setError('');
    try {
      const response = await fetch(`/api/files/${kind}/${encodeURIComponent(name)}`, { headers });
      if (!response.ok) throw new Error('下载失败，文件可能已删除或登录已过期。');
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url; link.download = name; document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (reason) { setError(reason instanceof Error ? reason.message : '下载失败'); }
  }

  return { state, connected, needsLogin, pending, error, clearError: () => setError(''), action: request, download };
}
