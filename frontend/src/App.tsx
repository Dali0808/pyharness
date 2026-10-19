import { useEffect, useRef, useState } from 'react';
import type { FormEvent, KeyboardEvent } from 'react';
import Markdown from 'react-markdown';
import { useWorkspace } from './api';
import type { Workspace } from './api';
import { Brand, Icon } from './icons';

const phaseLabels: Record<string, string> = { idle: '就绪', running: '运行中', stopping: '正在停止', completed: '已完成', cancelled: '已停止', failed: '运行失败' };
type Action = ReturnType<typeof useWorkspace>['action'];

export function App() {
  const { state, connected, needsLogin, pending, error, clearError, action, download } = useWorkspace();
  const [filesOpen, setFilesOpen] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [draft, setDraft] = useState('');
  const messageEnd = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const session = state?.active;
  const home = session?.messages.length === 0;
  const blocked = !connected || pending;

  useEffect(() => { if (state?.trusted) input.current?.focus(); }, [home, session?.id, state?.trusted]);
  useEffect(() => { messageEnd.current?.scrollIntoView({ block: 'end' }); }, [session?.messages.length, session?.live]);
  useEffect(() => {
    const focusSearch = (event: globalThis.KeyboardEvent) => {
      if (event.key !== '/' || event.ctrlKey || event.metaKey || document.querySelector('dialog[open]')) return;
      if (event.target instanceof HTMLElement && (event.target.matches('input, textarea') || event.target.isContentEditable)) return;
      event.preventDefault(); setNavOpen(true);
      requestAnimationFrame(() => document.querySelector<HTMLInputElement>('[aria-label="搜索会话"]')?.focus());
    };
    window.addEventListener('keydown', focusSearch);
    return () => window.removeEventListener('keydown', focusSearch);
  }, []);

  async function send(event?: FormEvent) {
    event?.preventDefault();
    if (!draft.trim() || state?.busy || blocked || !session) return;
    if (await action('tasks', { text: draft, session_id: session.id })) setDraft('');
  }
  function handleInputKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); void send(); }
  }
  async function newSession() {
    if (await action('sessions')) { setDraft(''); setQuery(''); setNavOpen(false); }
  }

  if (needsLogin) return <Login pending={pending} error={error} login={(username, password) => action('auth/login', { username, password })} />;
  if (!state || !session) return <main className="connection-screen"><Brand /><h1>连接智能助手</h1><p>{error || '正在连接服务…'}</p></main>;
  const errorBanner = error && <div className="connection-error" role="alert"><span>{error}</span><button className="icon-button" aria-label="关闭提示" onClick={clearError}><Icon name="close" size={15} /></button></div>;
  if (!state.trusted) return <main className="connection-screen"><Brand /><h1>开始使用资料空间</h1><p>上传代码和文本资料，让助手阅读、分析并生成可下载的报告。</p>{errorBanner}<button className="approve-button" disabled={blocked} onClick={() => void action('trust', { trusted: true })}>开始使用</button></main>;

  const accountActions = <div className="account-actions"><span className="model-button"><Icon name="spark" size={14} />{state.settings.model_id || '模型待配置'}</span><button className="resource-button" onClick={() => setFilesOpen(true)}><Icon name="folder" size={15} />资料与产物</button>{state.account && <button className="resource-button" disabled={pending} onClick={() => void action('auth/logout')}>退出登录</button>}</div>;
  const composer = <form className={`composer${state.busy ? ' busy' : ''}`} onSubmit={send}>
    <label className="sr-only" htmlFor="task-input">描述你的任务</label><textarea id="task-input" ref={input} value={draft} onChange={event => setDraft(event.target.value)} onKeyDown={handleInputKey} placeholder={state.busy ? '任务处理中，你可以先准备下一条消息…' : '阅读代码、整理文档，或从一个问题开始…'} rows={2} maxLength={8000} />
    <div className="composer-toolbar"><button className="composer-context" type="button" onClick={() => setFilesOpen(true)}><Icon name="folder" size={14} /><span>我的资料</span><span className="composer-divider" /><span>{state.files.documents.length} 个文件</span></button>{state.busy ? <button className="stop-button" disabled={blocked || session.phase === 'stopping'} type="button" onClick={() => void action('stop')}><Icon name="stop" size={13} />停止任务</button> : <button className="send-button" aria-label="发送任务" disabled={!draft.trim() || blocked} type="submit"><span className="send-inner"><Icon name="arrow" size={20} /></span></button>}</div>
  </form>;
  const filesDialog = filesOpen && <FilesDialog state={state} disabled={blocked || state.busy} action={action} download={download} close={() => setFilesOpen(false)} error={error} />;
  if (home) return <div className="home" data-session-id={session.id}>
    <header className="home-header"><div className="wordmark" aria-label="Lario"><Brand /><span>lario<span className="wordmark-dot">.</span></span></div>{accountActions}</header>
    {errorBanner}<main className="home-main" aria-label="开始新任务">
      <div className="welcome"><Brand /><h1><span>想清楚。</span><span>让资料成为答案。</span></h1><p>阅读代码、整理文档、处理文件，从一个问题开始。</p></div>
      {composer}<p className="home-caption">Enter 发送 · Shift + Enter 换行</p>
      {session.failure && <p className="run-failure" role="alert">{session.failure}</p>}
      {state.sessions.some(item => item.id !== session.id) && <div className="home-history"><span>继续最近的会话</span>{state.sessions.filter(item => item.id !== session.id).slice(-3).map(item => <button key={item.id} disabled={blocked} onClick={() => void action(`sessions/${item.id}/select`)}><Icon name="chat" size={14} />{item.title}</button>)}</div>}
    </main><footer className="home-footer">{state.account?.username || '本机资料空间'} · 上传资料由你管理 · 生成结果可下载</footer>{filesDialog}
  </div>;

  return <div data-session-id={session.id} className={`workbench review-closed${navOpen ? ' nav-open' : ''}`}>
    {navOpen && <button className="nav-backdrop" aria-label="关闭会话导航" onClick={() => setNavOpen(false)} />}
    <aside className="sidebar" aria-label="会话导航">
      <div className="wordmark"><Brand /><span>lario<span className="wordmark-dot">.</span></span><span className="web-tag">WEB</span></div>
      <div className="workspace-card"><span className="workspace-icon"><Icon name="folder" size={20} /></span><div><strong>{state.account?.username || '我的资料'}</strong><span>智能助手</span></div><span className="workspace-local" /></div>
      <button className="new-session" onClick={() => void newSession()} disabled={state.busy || blocked}><Icon name="plus" /><span>新建会话</span></button>
      <label className="session-search"><Icon name="search" size={15} /><input aria-label="搜索会话" placeholder="搜索会话" value={query} onChange={event => setQuery(event.target.value)} /><kbd>/</kbd></label>
      <div className="nav-section-heading">最近会话<span>{state.sessions.length}</span></div>
      <nav className="session-list" aria-label="最近会话">{state.sessions.filter(item => item.title.toLowerCase().includes(query.toLowerCase())).map(item => <div className="session-row" key={item.id}><button className={`session-link${item.id === session.id ? ' selected' : ''}`} aria-current={item.id === session.id ? 'page' : undefined} disabled={state.busy || blocked} onClick={async () => { if (await action(`sessions/${item.id}/select`)) { setDraft(''); setNavOpen(false); } }}><Icon name="chat" size={16} /><span>{item.title}</span></button><button className="icon-button delete-session" aria-label={`删除会话 ${item.title}`} disabled={state.busy || blocked} onClick={() => { if (confirm('删除此会话及其历史？此操作无法撤销。')) void action(`sessions/${item.id}`, {}, 'DELETE'); }}><Icon name="close" size={12} /></button></div>)}</nav>
      <div className="sidebar-bottom"><div className="prototype-note"><Icon name="spark" size={18} /><strong>从资料到答案</strong><p>上传、阅读、分析、导出。<br />本地编码请使用 Lario TUI。</p></div><button className="settings-link" onClick={() => setFilesOpen(true)}><Icon name="folder" size={17} />资料与产物</button><div className="sidebar-footnote"><span className="green-dot" />{connected ? '已连接' : '连接断开'}</div></div>
    </aside>
    <header className="topbar"><div className="breadcrumbs"><button className="icon-button mobile-nav" onClick={() => setNavOpen(!navOpen)} aria-label="打开会话导航"><Icon name="panel" /></button><span>智能助手</span></div>{accountActions}</header>
    <main className="conversation" aria-label="任务对话">
      <div className="conversation-heading"><div><div className="eyebrow">ASSISTANT SESSION</div><h1>{session.title}</h1></div><span className={`status status-${session.phase}`} role="status"><span className="status-dot" />{phaseLabels[session.phase] || session.phase}</span></div>
      {errorBanner}<div className="message-scroll"><div className="demo-notice"><Icon name="shield" size={16} /><span>助手阅读你上传的资料，报告和处理结果可在「资料与产物」下载。</span></div>
        <div className="messages">{session.messages.map((message, index) => <Message key={`${session.id}-${index}`} role={message.role} text={message.text} />)}{session.live && <Message role="assistant" text={session.live} live />}
          {state.busy && !session.live && <p className="thinking">正在处理任务…</p>}
          {session.failure && <p className="run-failure" role="alert">{session.failure}</p>}
          {session.notices.trim() && <pre className="run-notices">{session.notices.trim()}</pre>}
          {!!session.tools.length && <section className="tool-steps" aria-label="执行步骤"><div className="tool-steps-heading">执行过程<span className="small-tag">{session.steps} 步</span></div>{session.tools.map((tool, index) => <details className="tool-step" key={`${tool.id}-${index}`}><summary><Icon name={tool.status === 'completed' ? 'check' : 'clock'} size={13} /><span>{tool.name}</span><span className="step-detail">{tool.status}</span></summary><pre>{tool.arguments}</pre>{tool.output && <pre>{tool.output}</pre>}</details>)}</section>}
          {session.usage.total_tokens > 0 && <p className="usage-note">本次运行 {session.usage.total_tokens} tokens · {session.steps} 步</p>}
        </div><div ref={messageEnd} />
      </div>{composer}<div className="composer-caption"><span>Enter 发送 · Shift + Enter 换行</span><span>会话自动保存</span></div>
    </main>{filesDialog}
  </div>;
}

function Login({ pending, error, login }: { pending: boolean; error: string; login: (username: string, password: string) => Promise<boolean> }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  async function submit(event: FormEvent) { event.preventDefault(); if (await login(username, password)) setPassword(''); }
  return <main className="connection-screen"><Brand /><h1>登录智能助手</h1><p>使用管理员为你创建的账号。</p><form className="login-form" onSubmit={submit}><label>账号<input autoComplete="username" required maxLength={64} value={username} onChange={event => setUsername(event.target.value)} /></label><label>密码<input type="password" autoComplete="current-password" required maxLength={1024} value={password} onChange={event => setPassword(event.target.value)} /></label>{error && <p className="run-failure" role="alert">{error}</p>}<button className="approve-button" disabled={pending} type="submit">{pending ? '正在登录…' : '登录'}</button></form></main>;
}

function FilesDialog({ state, disabled, action, download, close, error }: { state: Workspace; disabled: boolean; action: Action; download: (kind: string, name: string) => Promise<void>; close: () => void; error: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState('');
  useEffect(() => { dialog.current?.showModal(); }, []);
  async function upload(files: FileList | null) {
    if (!files) return;
    setUploading(true); setUploadError('');
    try {
      for (const file of Array.from(files)) {
        if (file.size > state.files.limits.max_file_bytes) { setUploadError(`${file.name} 超过单文件大小限制。`); break; }
        if (!await action(`files/documents/${encodeURIComponent(file.name)}`, file)) break;
      }
    } finally { setUploading(false); }
  }
  return <dialog ref={dialog} className="settings-dialog file-dialog" onCancel={close} aria-labelledby="files-heading"><div className="settings-heading"><h2 id="files-heading">资料与产物</h2><button className="icon-button" aria-label="关闭资料面板" onClick={close}><Icon name="close" /></button></div>
    <p className="settings-description">上传 UTF-8 代码、文本、Markdown、JSON 或 CSV。单文件最多 {state.files.limits.max_file_bytes.toLocaleString()} 字节，同名文件不会被覆盖。</p>
    <label className="upload-control">上传资料<input type="file" multiple disabled={disabled || uploading} accept=".txt,.md,.json,.csv,.py,.js,.jsx,.ts,.tsx,.css,.html,.xml,.yaml,.yml,.toml,.ini,.cfg,.sql,.sh,.rs,.go,.java,.c,.h,.cpp,.hpp,.log" onChange={event => { void upload(event.target.files); event.target.value = ''; }} /></label>
    {(error || uploadError) && <p className="run-failure" role="alert">{uploadError || error}</p>}
    <p className="storage-usage">存储 {state.files.bytes.toLocaleString()} / {state.files.limits.max_storage_bytes.toLocaleString()} 字节</p>
    {(['documents', 'artifacts'] as const).map(kind => <section className="file-section" key={kind}><h3>{kind === 'documents' ? '我的资料' : '生成产物'}</h3>{state.files[kind].length === 0 && <p className="field-help">{kind === 'documents' ? '上传资料后，助手就可以阅读它们。' : '让助手生成报告或处理文件，结果会出现在这里。'}</p>}{state.files[kind].map(file => <div className="file-row" key={file.name}><span><strong>{file.name}</strong><small>{file.size.toLocaleString()} 字节</small></span><button className="resource-button" onClick={() => void download(kind, file.name)} aria-label={`下载 ${file.name}`}>下载</button><button className="resource-button" disabled={disabled || uploading} onClick={() => { if (confirm(`删除 ${file.name}？此操作无法撤销。会话中已经引用的内容会保留到删除会话。`)) void action(`files/${kind}/${encodeURIComponent(file.name)}`, {}, 'DELETE'); }} aria-label={`删除 ${file.name}`}>删除</button></div>)}</section>)}
    {state.account && <section className="file-section"><h3>今日用量（UTC）</h3><p>模型请求 {state.account.usage.calls} / {state.account.limits.daily_requests}</p><p>配额占用 {state.account.usage.charged_tokens.toLocaleString()} / {state.account.limits.daily_tokens.toLocaleString()} tokens</p><p className="field-help">实际已返回用量 {state.account.usage.actual_tokens.toLocaleString()} tokens。请求前预留预算，未知用量保留预留额度。</p></section>}
  </dialog>;
}

function Message({ role, text, live = false }: { role: string; text: string; live?: boolean }) {
  const user = role === 'user';
  return <article className={`message ${user ? 'user' : 'assistant'}`}><span className={`avatar ${user ? 'user-avatar' : 'assistant-avatar'}`}>{user ? '你' : ''}</span><div className="message-body"><div className="message-meta"><strong>{user ? '你' : 'Lario'}</strong><span>{role === 'partial' ? '未保存的部分回复' : live ? '正在回复' : '当前会话'}</span></div><div className="message-text">{user ? text : <Markdown skipHtml disallowedElements={['img']}>{text}</Markdown>}</div></div></article>;
}
