import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { readFile } from 'node:fs/promises';

const origin = 'http://127.0.0.1:4173';
const headers = { Origin: origin };
const password = 'browser-only-password-2026';

async function signIn(page: Page, username = 'alice') {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '登录智能助手' })).toBeVisible();
  await page.getByLabel('账号', { exact: true }).fill(username);
  await page.getByLabel('密码', { exact: true }).fill(password);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.getByRole('main', { name: '开始新任务' })).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await page.route(/^https:\/\/fonts\.(googleapis|gstatic)\.com\//, route => route.fulfill({ contentType: 'text/css', body: '' }));
});

test('login rejects wrong password, cookie persists reload, logout revokes it', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('账号', { exact: true }).fill('alice');
  await page.getByLabel('密码', { exact: true }).fill('wrong-but-long-password');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('账号或密码错误');
  await page.getByLabel('密码', { exact: true }).fill(password);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.getByRole('button', { name: '退出登录' })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('button', { name: '退出登录' })).toBeVisible();
  await page.getByRole('button', { name: '退出登录' }).click();
  await expect(page.getByRole('heading', { name: '登录智能助手' })).toBeVisible();
  expect((await page.request.get('/api/state')).status()).toBe(401);
});

test.describe('assistant', () => {
  test.beforeEach(async ({ page }) => {
    // Account state is intentionally persistent across requests; start each case with clean inputs.
    await page.goto('/');
    await page.getByLabel('账号', { exact: true }).fill('alice');
    await page.getByLabel('密码', { exact: true }).fill(password);
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await expect(page.getByRole('button', { name: '退出登录' })).toBeVisible();
    await page.request.post('/api/stop', { headers, data: {} });
    const state = await (await page.request.get('/api/state')).json();
    for (const kind of ['documents', 'artifacts']) {
      for (const file of state.files[kind]) await page.request.delete(`/api/files/${kind}/${encodeURIComponent(file.name)}`, { headers });
    }
    const next = await (await page.request.post('/api/sessions', { headers, data: {} })).json();
    await expect(page.locator('.home')).toHaveAttribute('data-session-id', next.active.id);
  });

  test('upload, read, create artifact and download through real account API', async ({ page }, testInfo) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.screenshot({ path: testInfo.outputPath('home.png'), fullPage: true, animations: 'disabled' });
    await page.getByRole('button', { name: '资料与产物', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('上传资料').setInputFiles({ name: 'README.md', mimeType: 'text/markdown', buffer: Buffer.from('# Example') });
    await expect(dialog.getByText('README.md', { exact: true })).toBeVisible();
    await dialog.getByRole('button', { name: '关闭资料面板' }).click();
    await page.getByRole('textbox', { name: '描述你的任务' }).fill('读取 README 并生成报告');
    await page.getByRole('button', { name: '发送任务', exact: true }).click();
    await expect(page.getByRole('status')).toHaveText('已完成');
    await expect(page.locator('.message.assistant strong').filter({ hasText: '结果' })).toBeVisible();
    const state = await (await page.request.get('/api/state')).json();
    expect(state.active.tools.map((tool: { name: string }) => tool.name)).toEqual(['read_file', 'create_artifact']);
    const name = state.files.artifacts[0].name;
    await page.getByRole('button', { name: '资料与产物', exact: true }).first().click();
    await expect(dialog.getByText('今日用量（UTC）')).toBeVisible();
    const downloaded = page.waitForEvent('download');
    await dialog.getByRole('button', { name: `下载 ${name}`, exact: true }).click();
    const download = await downloaded;
    expect(download.suggestedFilename()).toBe(name);
    expect(await readFile((await download.path())!, 'utf8')).toContain('# 测试报告');
    await page.screenshot({ path: testInfo.outputPath('files.png'), fullPage: true, animations: 'disabled' });
    await dialog.getByRole('button', { name: '关闭资料面板' }).click();
    await page.reload();
    await expect(page.getByRole('status')).toHaveText('已完成');
    await expect(page.locator('.message.user')).toHaveCount(1);
    await page.screenshot({ path: testInfo.outputPath('desktop.png'), fullPage: true, animations: 'disabled' });
    expect(errors).toEqual([]);
  });

  test('same-name upload is rejected and explicit deletion removes the file', async ({ page }) => {
    await page.getByRole('button', { name: '资料与产物', exact: true }).click();
    const dialog = page.getByRole('dialog');
    const file = { name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('first') };
    await dialog.getByLabel('上传资料').setInputFiles(file);
    await expect(dialog.getByText('notes.txt', { exact: true })).toBeVisible();
    await dialog.getByLabel('上传资料').setInputFiles(file);
    await expect(dialog.getByRole('alert')).toContainText('FileExistsError');
    page.once('dialog', prompt => prompt.accept());
    await dialog.getByRole('button', { name: '删除 notes.txt', exact: true }).click();
    await expect(dialog.getByText('notes.txt', { exact: true })).toHaveCount(0);
  });

  test('forbidden coding tool returns error without writes or approval UI', async ({ page }) => {
    await page.getByRole('textbox', { name: '描述你的任务' }).fill('禁止编码');
    await page.getByRole('button', { name: '发送任务', exact: true }).click();
    await expect(page.getByRole('status')).toHaveText('已完成');
    const state = await (await page.request.get('/api/state')).json();
    expect(state.active.tools[0].status).toBe('error');
    expect(state.active.tools[0].output).toContain('unknown_tool');
    expect(state.files.documents).toHaveLength(0);
    await expect(page.getByRole('button', { name: '批准修改', exact: true })).toHaveCount(0);
  });

  test('mobile keeps IME and multiline, escapes user input and stops partial stream', async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const input = page.getByRole('textbox', { name: '描述你的任务' });
    const text = '<img src=x onerror="window.compromised=true">';
    await input.fill(text);
    await input.press('Shift+Enter');
    await input.dispatchEvent('keydown', { key: 'Enter', code: 'Enter', isComposing: true });
    await expect(page.locator('.home')).toBeVisible();
    await input.press('Enter');
    await expect(page.getByRole('status')).toHaveText('已完成');
    await expect(page.locator('.message.user .message-text')).toHaveText(text);
    await expect(page.locator('.message.user img')).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath('mobile.png'), fullPage: true, animations: 'disabled' });
    await page.getByRole('button', { name: '打开会话导航' }).click();
    await page.getByRole('button', { name: '新建会话', exact: true }).click();
    await expect(page.locator('.home')).toBeVisible();
    await input.fill('等待流式回复');
    await input.press('Enter');
    await expect(page.locator('.message.assistant .message-text')).toContainText('未完成的流式文本');
    await page.getByRole('button', { name: '停止任务' }).click();
    await expect(page.getByRole('status')).toHaveText('已停止');
    await expect(page.getByText('未保存的部分回复', { exact: true })).toBeVisible();
  });

  test('second account cannot see uploaded files, sessions or artifacts', async ({ page, browser }) => {
    await page.request.post('/api/files/documents/private.txt', { headers: { ...headers, 'Content-Type': 'application/octet-stream' }, data: 'alice-only' });
    const created = await (await page.request.post('/api/sessions', { headers, data: {} })).json();
    const context = await browser.newContext({ baseURL: origin });
    const bob = await context.newPage();
    try {
      await signIn(bob, 'bob');
      const state = await (await bob.request.get('/api/state')).json();
      expect(state.account.username).toBe('bob');
      expect(state.files.documents).toHaveLength(0);
      expect((await bob.request.get('/api/files/documents/private.txt')).status()).toBe(404);
      expect((await bob.request.post(`/api/sessions/${created.active.id}/select`, { headers, data: {} })).status()).toBe(404);
    } finally { await context.close(); }
  });

  test('reference theme keeps gradient, neutral background and reduced motion', async ({ page }, testInfo) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await expect(page.locator('.home')).toHaveCSS('background-color', 'rgb(12, 12, 12)');
    await expect(page.locator('.wordmark .brand-mark line')).toHaveCount(24);
    await page.getByRole('textbox', { name: '描述你的任务' }).fill('分析资料');
    const gradient = await page.locator('.composer').evaluate(element => getComputedStyle(element, '::after').backgroundImage);
    expect(gradient).toContain('rgb(255, 212, 0)');
    expect(gradient).toContain('rgb(120, 208, 205)');
    const send = page.getByRole('button', { name: '发送任务', exact: true });
    expect(await send.evaluate(element => getComputedStyle(element, '::before').animationName)).toBe('none');
    await page.screenshot({ path: testInfo.outputPath('welcome.png'), fullPage: true, animations: 'disabled' });
  });
});
