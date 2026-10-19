import { useEffect, useRef, useState } from 'react';
import type { FormEvent, KeyboardEvent } from 'react';
import Markdown from 'react-markdown';
import { useWorkspace } from './api';
import type { Settings, Tool, Workspace } from './api';
import { Brand, Icon } from './icons';

const phaseLabels: Record<string, string> = {
  idle: '就绪', running: '运行中', awaiting_edit: '等待修改审批', awaiting_command: '等待命令审批',
  stopping: '正在停止', completed: '已完成', cancelled: '已停止', failed: '运行失败',
};

export function App() {
  const { state, connected, pending, error, clearError, action } = useWorkspace();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(innerWidth > 1100);
  const [navOpen, setNavOpen] = useState(false);
  const [tab, setTab] = useState<'diff' | 'command'>('diff');
  const [query, setQuery] = useState('');
  const [draft, setDraft] = useState('');
  const messageEnd = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const session = state?.active;
  const home = session?.messages.length === 0;
  const approval = session?.approval;
  const blocked = !connected || pending;

  useEffect(() => { if (state?.trusted) input.current?.focus(); }, [home, session?.id, state?.trusted]);
  useEffect(() => {
    if (approval) { setTab(approval.kind === 'command' ? 'command' : 'diff'); setReviewOpen(true); }
  }, [approval?.id]);
  useEffect(() => { messageEnd.current?.scrollIntoView({ block: 'end' }); }, [session?.messages.length, session?.live]);
  useEffect(() => {
    const focusSearch = (event: globalThis.KeyboardEvent) => {
      if (event.key !== '/' || event.ctrlKey || event.metaKey || !searchInput.current || document.querySelector('dialog[open]')) return;
      const target = event.target;
      if (target instanceof HTMLElement && (target.matches('input, textarea') || target.isContentEditable)) return;
      event.preventDefault(); setNavOpen(true);
      requestAnimationFrame(() => searchInput.current?.focus());
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

  const errorBanner = error && <div className="connection-error" role="alert"><span>{error}</span><button className="icon-button" aria-label="关闭提示" onClick={clearError}><Icon name="close" size={15} /></button></div>;
  if (!state) return <main className="connection-screen"><Brand /><h1>连接你的编码工作区</h1><p>{error || '正在连接 Python 服务…'}</p><p>使用终端运行 lario-web，并打开它提供的完整地址。</p></main>;
  if (!state.trusted) return <main className="connection-screen"><Brand /><h1>信任这个工作区？</h1><code>{state.workspace}</code><p>助手可以读取这里的文件。每次修改文件和执行 Docker 命令前，都会请求你的批准。</p>{errorBanner}<button className="approve-button" disabled={blocked} onClick={() => void action('trust', { trusted: true })}>信任并继续</button></main>;
  if (!session) return null;

  const modelButton = <button className="model-button" onClick={() => setSettingsOpen(true)} disabled={state.busy || blocked} aria-label="配置当前模型"><Icon name="spark" size={14} /><span>{state.settings.model_id || '配置模型'}</span><Icon name="down" size={13} /></button>;
  const composer = <form className={`composer${state.busy ? ' busy' : ''}`} onSubmit={send}>
    <label className="sr-only" htmlFor="task-input">描述你的任务</label><textarea id="task-input" ref={input} value={draft} onChange={event => setDraft(event.target.value)} onKeyDown={handleInputKey} placeholder={state.busy ? '任务处理中，你可以先准备下一条消息…' : '描述任务，或从一个问题开始…'} rows={2} maxLength={8000} />
    <div className="composer-toolbar"><div className="composer-context"><Icon name="folder" size={14} /><span className="composer-workspace-name">{state.workspace.split(/[\\/]/).pop()}</span><span className="composer-divider" /><span>本地工作区</span></div>{state.busy ? <button className="stop-button" disabled={blocked || session.phase === 'stopping'} type="button" onClick={() => void action('stop')}><Icon name="stop" size={13} />停止任务</button> : <button className="send-button" aria-label="发送任务" disabled={!draft.trim() || blocked} type="submit"><span className="send-inner"><Icon name="arrow" size={20} /></span></button>}</div>
  </form>;
  const settingsDialog = settingsOpen && <SettingsDialog settings={state.settings} keySet={state.api_key_set} pending={pending} error={error} onSave={async value => { if (await action('settings', value)) setSettingsOpen(false); }} onClose={() => setSettingsOpen(false)} />;

  if (home) return <div className="home" data-session-id={session.id}>
    <header className="home-header"><div className="wordmark" aria-label="Lario"><Brand /><span>lario<span className="wordmark-dot">.</span></span></div>{modelButton}</header>
    {errorBanner}
    <main className="home-main" aria-label="开始新任务">
      <div className="welcome"><Brand /><h1><span>想清楚。</span><span>然后，写出好代码。</span></h1><p>从一个问题开始，让想法变成代码。</p></div>
      {composer}<p className="home-caption">Enter 发送 · Shift + Enter 换行</p>
      {session.failure && <div className="home-history"><p className="run-failure" role="alert">{session.failure}</p><button disabled={blocked} onClick={() => void newSession()}>新建会话</button></div>}
      {state.sessions.some(item => item.id !== session.id) && <div className="home-history"><span>继续最近的会话</span>{state.sessions.filter(item => item.id !== session.id).slice(0, 3).map(item => <button key={item.id} disabled={blocked} onClick={() => void action(`sessions/${item.id}/select`)}><Icon name="chat" size={14} />{item.title}</button>)}</div>}
    </main>
    <footer className="home-footer">本机运行 · 文件修改需审批 · 会话保存为 JSONL</footer>{settingsDialog}
  </div>;

  return <div data-session-id={session.id} className={`workbench${reviewOpen ? '' : ' review-closed'}${navOpen ? ' nav-open' : ''}`}>
    {navOpen && <button className="nav-backdrop" aria-label="关闭会话导航" onClick={() => setNavOpen(false)} />}
    <aside className="sidebar" aria-label="会话导航">
      <div className="wordmark" aria-label="Lario 工作台"><Brand /><span>lario<span className="wordmark-dot">.</span></span><span className="web-tag">WEB</span></div>
      <div className="workspace-card" title={state.workspace}><span className="workspace-icon"><Icon name="folder" size={20} /></span><div><strong>{state.workspace.split(/[\\/]/).pop()}</strong><span>已信任的本地工作区</span></div><span className="workspace-local" /></div>
      <button className="new-session" onClick={() => void newSession()} disabled={state.busy || blocked}><Icon name="plus" /><span>新建会话</span></button>
      <label className="session-search"><Icon name="search" size={15} /><input ref={searchInput} aria-label="搜索会话" placeholder="搜索会话" value={query} onChange={event => setQuery(event.target.value)} /><kbd>/</kbd></label>
      <div className="nav-section-heading">最近会话<span>{state.sessions.length}</span></div>
      <nav className="session-list" aria-label="最近会话">{state.sessions.filter(item => item.title.toLowerCase().includes(query.toLowerCase())).map(item => <button key={item.id} className={`session-link${item.id === session.id ? ' selected' : ''}`} aria-current={item.id === session.id ? 'page' : undefined} disabled={state.busy || blocked} title={item.path} onClick={async () => { if (await action(`sessions/${item.id}/select`)) { setDraft(''); setNavOpen(false); } }}><Icon name="chat" size={16} /><span>{item.title}</span>{item.id === session.id && <span className="session-selected-dot" />}</button>)}</nav>
      <div className="sidebar-bottom"><div className="prototype-note"><span className="prototype-symbol"><Icon name="spark" size={18} /></span><strong>从一个小任务开始</strong><p>定位、修改、审查、验证。<br />每一步都在你的掌握之中。</p><span>与 TUI 共用执行入口</span></div><button className="settings-link" disabled={state.busy || blocked} onClick={() => setSettingsOpen(true)}><Icon name="settings" size={17} />项目设置<Icon name="chevron" size={14} /></button><div className="sidebar-footnote"><span className="green-dot" />{connected ? '已连接' : '连接断开'}<span>v0.2</span></div></div>
    </aside>
    <header className="topbar"><div className="breadcrumbs"><button className="icon-button mobile-nav" onClick={() => setNavOpen(!navOpen)} aria-label="打开会话导航"><Icon name="panel" /></button><Icon name="folder" size={17} /><span>工作区</span><span className="breadcrumb-slash">/</span><span className="breadcrumb-current">工作台</span></div><div className="topbar-actions"><span className="demo-badge"><span />{connected ? '本机运行' : '连接断开'}</span>{modelButton}<span className="topbar-divider" /><button className={`icon-button${reviewOpen ? ' active' : ''}`} onClick={() => setReviewOpen(!reviewOpen)} aria-label={reviewOpen ? '收起审查面板' : '打开审查面板'} aria-expanded={reviewOpen}><Icon name="panel" /></button></div></header>
    <main className="conversation" aria-label="任务对话">
      <div className="conversation-heading"><div><div className="eyebrow">CODING SESSION</div><h1>{session.title}</h1><p title={session.path}>{session.path}</p></div><span className={`status status-${session.phase}`} role="status"><span className="status-dot" />{phaseLabels[session.phase] || session.phase}</span></div>
      {errorBanner}
      <div className="message-scroll"><div className="demo-notice"><Icon name="shield" size={16} /><span>文件修改经审批后写入本地；命令在 Docker 临时副本执行，产物不回写。</span></div>
        <div className="messages">{session.messages.map((message, index) => <Message key={`${session.id}-${index}`} role={message.role} text={message.text} />)}
          {session.live && <Message role="assistant" text={session.live} live />}
          {state.busy && !session.live && <p className="thinking">{approval ? '等待你的审批…' : '正在处理任务…'}</p>}
          {session.failure && <p className="run-failure" role="alert">{session.failure}</p>}
          {session.notices.trim() && <pre className="run-notices">{session.notices.trim()}</pre>}
          {!!session.tools.length && <section className="tool-steps" aria-label="执行步骤"><div className="tool-steps-heading">执行过程<span className="small-tag">{session.steps} 步</span></div>{session.tools.map((tool, index) => <details className="tool-step" key={`${tool.id}-${index}`}><summary><span className="step-icon"><Icon name={tool.status === 'completed' ? 'check' : tool.status === 'error' ? 'close' : 'clock'} size={13} /></span><span>{tool.name}</span><span className="step-detail">{tool.status}</span><Icon name="chevron" size={13} /></summary><pre>{tool.arguments}</pre>{tool.output && <pre>{tool.output}</pre>}</details>)}{approval && <button className="review-step" onClick={() => setReviewOpen(true)}><Icon name="shield" /><span><strong>有一项操作等待审批</strong><small>点击查看完整内容</small></span><Icon name="chevron" size={15} /></button>}</section>}
          {session.usage.total_tokens > 0 && <p className="usage-note">本次运行 {session.usage.total_tokens} tokens · {session.steps} 步</p>}
        </div><div ref={messageEnd} />
      </div>{composer}<div className="composer-caption"><span>Enter 发送 · Shift + Enter 换行</span><span>完整历史保存在本地 JSONL</span></div>
    </main>
    <aside className="review-panel" aria-label="审查面板" hidden={!reviewOpen}>
      <div className="review-tabs" role="tablist" aria-label="审查内容" onKeyDown={event => { if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); const next = tab === 'diff' ? 'command' : 'diff'; setTab(next); document.getElementById(`${next}-tab`)?.focus(); } }}><button id="diff-tab" role="tab" tabIndex={tab === 'diff' ? 0 : -1} aria-controls="diff-panel" aria-selected={tab === 'diff'} onClick={() => setTab('diff')}><Icon name="file" size={15} />变更</button><button id="command-tab" role="tab" tabIndex={tab === 'command' ? 0 : -1} aria-controls="command-panel" aria-selected={tab === 'command'} onClick={() => setTab('command')}><Icon name="terminal" size={16} />执行结果</button><button className="icon-button review-mobile-close" aria-label="关闭审查面板" onClick={() => setReviewOpen(false)}><Icon name="close" size={16} /></button></div>
      <Review state={state} tab={tab} disabled={blocked} decide={approved => approval && void action(`approvals/${approval.id}`, { approved, run_id: approval.run_id })} />
      <div className="review-footer"><Icon name="shield" size={14} /><span>批准仅对当前操作生效 · 停止后审批失效</span></div>
    </aside>{settingsDialog}
  </div>;
}

function Message({ role, text, live = false }: { role: string; text: string; live?: boolean }) {
  return <article className={`message ${role === 'user' ? 'user' : 'assistant'}`}><div className="message-avatar">{role === 'user' ? <span className="user-avatar">你</span> : <Brand small />}</div><div className="message-body"><div className="message-meta"><strong>{role === 'user' ? '你' : 'Lario'}</strong><span>{role === 'partial' ? '未保存的部分回复' : live ? '正在生成' : '当前会话'}</span></div><div className="message-text">{role === 'user' ? text : <Markdown skipHtml components={{ img: ({ alt }) => <span>[图片：{alt}]</span>, a: ({ children, href }) => <a href={href} target="_blank" rel="noreferrer">{children}</a> }}>{text}</Markdown>}</div></div></article>;
}

function Review({ state, tab, disabled, decide }: { state: Workspace; tab: 'diff' | 'command'; disabled: boolean; decide: (approved: boolean) => void }) {
  const approval = state.active.approval;
  const relevant = approval?.kind === (tab === 'diff' ? 'edit' : 'command') ? approval : null;
  const tools = state.active.tools.filter(tool => tab === 'command' ? tool.name === 'run_command' : ['write_file', 'edit_file'].includes(tool.name));
  if (!relevant && !tools.length) return <div id={`${tab}-panel`} role="tabpanel" aria-labelledby={`${tab}-tab`} className="review-empty"><span className="empty-icon"><Icon name={tab === 'diff' ? 'file' : 'terminal'} size={27} /></span><h2>{tab === 'diff' ? '变更会出现在这里' : '在这里查看命令结果'}</h2><p>助手提出操作后，在这里查看并审批。</p></div>;
  return <div id={`${tab}-panel`} role="tabpanel" aria-labelledby={`${tab}-tab`} className="review-content"><div className="panel-heading"><div className="eyebrow">{tab === 'diff' ? 'CHANGE REVIEW' : 'VERIFICATION'}</div><h2>{relevant ? tab === 'diff' ? '审查这次文件修改' : '确认执行命令' : '最近的操作记录'}</h2><p>{tab === 'diff' ? '批准后会修改当前工作区文件。' : '在禁网的 Docker 工作区副本运行，产物不回写。'}</p></div>
    {relevant ? <>{relevant.path && <p className="review-target">文件：{relevant.path}</p>}<ReviewDetail text={relevant.detail} diff={tab === 'diff'} /><div className="approval-actions"><button className="reject-button" disabled={disabled} onClick={() => decide(false)}>拒绝</button><button className="approve-button" disabled={disabled} onClick={() => decide(true)}><Icon name="check" size={16} />{tab === 'diff' ? '批准修改' : '批准执行命令'}</button></div></> : tools.slice(-10).map((tool: Tool, index) => <section className="real-tool-result" key={`${tool.id}-${index}`}><strong>{tool.name} · {tool.status}</strong><ReviewDetail text={tool.detail || tool.arguments} diff={tab === 'diff' && !!tool.detail} />{tool.output && <pre className={tab === 'command' ? 'terminal-output' : 'tool-output'}>{tool.output}</pre>}</section>)}
  </div>;
}

function ReviewDetail({ text, diff }: { text: string; diff: boolean }) {
  return <pre className={`review-detail${diff ? ' unified-diff' : ''}`} aria-label={diff ? '文件修改内容' : '命令详情'}>{diff ? text.split('\n').map((line, i) => <span key={i} className={line.startsWith('+') ? 'added' : line.startsWith('-') ? 'removed' : ''}>{line}{'\n'}</span>) : text}</pre>;
}

function SettingsDialog({ settings, keySet, pending, error, onSave, onClose }: { settings: Settings; keySet: boolean; pending: boolean; error: string; onSave: (settings: Settings) => Promise<void>; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { dialog.current?.showModal(); }, []);
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    void onSave({ model_id: String(values.get('model')).trim(), provider_id: String(values.get('provider')).trim(), base_url: String(values.get('baseUrl')), api_key_env: String(values.get('apiKeyEnv')), max_steps: Number(values.get('maxSteps')), context_window: values.get('contextWindow') ? Number(values.get('contextWindow')) : null, multi_agent: values.get('multiAgent') === 'on', session: String(values.get('session')) });
  }
  return <dialog className="settings-dialog" ref={dialog} onCancel={onClose} aria-labelledby="settings-heading"><form onSubmit={submit}><div className="settings-heading"><span className="settings-icon"><Icon name="settings" size={21} /></span><button className="icon-button" type="button" aria-label="关闭设置" onClick={onClose}><Icon name="close" /></button></div><div className="eyebrow">WORKSPACE SETTINGS</div><h2 id="settings-heading">项目设置</h2><p className="settings-description">与 TUI 共用 .runtime/lario.json。更换模型后请新建会话。</p><label>模型名称<input name="model" required pattern="\S+" maxLength={100} defaultValue={settings.model_id} /></label><label>Provider ID<input name="provider" required pattern="\S+" maxLength={100} defaultValue={settings.provider_id} /></label><label>API 地址<input name="baseUrl" type="url" pattern="https?://.*" required defaultValue={settings.base_url} /></label><label>密钥环境变量名<input name="apiKeyEnv" aria-label="密钥环境变量名" required pattern="[A-Za-z_][A-Za-z0-9_]*" maxLength={100} defaultValue={settings.api_key_env} /><span className="field-help">启动进程中{keySet ? '已设置' : '未设置'} {settings.api_key_env}。这里只填写变量名。</span></label><div className="settings-grid"><label>最大模型步数<input name="maxSteps" type="number" required min={1} max={100} defaultValue={settings.max_steps} /></label><label>上下文窗口<input name="contextWindow" type="number" min={1024} max={2000000} step={1} placeholder="留空关闭压缩" defaultValue={settings.context_window ?? ''} /></label></div><label>Session 路径<input name="session" aria-label="Session 路径" required maxLength={500} defaultValue={settings.session} /><span className="field-help">工作区内的相对路径，可以继续已有 TUI 会话。</span></label><label className="checkbox-label"><input name="multiAgent" type="checkbox" defaultChecked={settings.multi_agent} />启用只读子 Agent</label><div className="settings-disclaimer"><Icon name="shield" size={16} /><span>配置持久保存在当前工作区。API Key 只由 Python 从环境变量读取，网页不会接收密钥值。</span></div>{error && <p className="run-failure" role="alert">{error}</p>}<div className="settings-actions"><button className="reject-button" type="button" onClick={onClose}>取消</button><button className="approve-button" disabled={pending} type="submit">保存设置</button></div></form></dialog>;
}
