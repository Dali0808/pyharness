import { expect, test } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

const headers = { Authorization: 'Bearer browser-test-token' };

test.beforeEach(async ({ page }) => {
  await page.route(/^https:\/\/fonts\.(googleapis|gstatic)\.com\//, route => route.fulfill({ contentType: 'text/css', body: '' }));
  await page.goto('/#token=browser-test-token');
  await page.request.post('/api/stop', { headers });
  await page.request.post('/api/trust', { headers, data: { trusted: true } });
  const response = await page.request.post('/api/sessions', { headers, data: {} });
  const state = await response.json();
  await expect.poll(async () => {
    const latest = await page.request.get('/api/state', { headers });
    return (await latest.json()).active.id;
  }).toBe(state.active.id);
  await expect(page.getByRole('main', { name: '开始新任务' })).toBeVisible();
  await expect(page.locator('.home')).toHaveAttribute('data-session-id', state.active.id);
});

test('home starts a real backend task, approvals write files and command shows actual output', async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await expect(page.getByRole('button', { name: '发送任务', exact: true })).toBeDisabled();
  await expect(page.getByRole('complementary')).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath('home.png'), fullPage: true, animations: 'disabled' });
  await page.getByRole('textbox', { name: '描述你的任务' }).fill('创建文件并运行检查');
  await page.getByRole('button', { name: '发送任务', exact: true }).click();
  await expect(page.locator('.message.user .message-text')).toHaveText('创建文件并运行检查');
  await expect(page.getByRole('button', { name: '批准修改', exact: true })).toBeVisible();
  await expect(page.getByLabel('文件修改内容')).toContainText('written-after-approval');
  await page.reload();
  await expect(page.getByRole('button', { name: '批准修改', exact: true })).toBeVisible();
  await expect(page.locator('.message.user')).toHaveCount(1);
  await page.screenshot({ path: testInfo.outputPath('desktop.png'), fullPage: true, animations: 'disabled' });
  await page.getByRole('button', { name: '批准修改', exact: true }).click();
  await expect(page.getByRole('button', { name: '批准执行命令' })).toBeVisible();
  await expect(page.getByLabel('命令详情')).toContainText('python -V');
  const state = await (await page.request.get('/api/state', { headers })).json();
  const createdPath = JSON.parse(state.active.tools.find((tool: { name: string }) => tool.name === 'write_file').arguments).path;
  expect(await readFile(join(state.workspace, createdPath), 'utf8')).toBe('written-after-approval');
  await page.getByRole('button', { name: '批准执行命令' }).click();
  await expect(page.getByRole('status')).toHaveText('已完成');
  await expect(page.locator('.terminal-output')).toContainText('exit_code: 17');
  await expect(page.locator('.message.assistant strong').filter({ hasText: '结果' })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('status')).toHaveText('已完成');
  await expect(page.locator('.message.user')).toHaveCount(1);
  expect(errors).toEqual([]);
});

test('multiline and IME stay on home, reject continues model and new session returns home', async ({ page }) => {
  const input = page.getByRole('textbox', { name: '描述你的任务' });
  await input.fill('拒绝文件修改');
  await input.press('Shift+Enter');
  await input.pressSequentially('second line');
  await input.dispatchEvent('keydown', { key: 'Enter', code: 'Enter', isComposing: true });
  await expect(page.locator('.home')).toBeVisible();
  await expect(input).toHaveValue('拒绝文件修改\nsecond line');
  await input.press('Enter');
  await expect(page.getByRole('button', { name: '批准修改', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '拒绝', exact: true }).click();
  await expect(page.getByRole('status')).toHaveText('已完成');
  await expect(page.getByRole('button', { name: '批准修改', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '新建会话' }).click();
  await expect(page.locator('.home')).toBeVisible();
  await expect(input).toHaveValue('');
  await expect(input).toBeFocused();
});

test('settings persist on backend and API keys are never sent to browser', async ({ page }) => {
  await page.getByRole('button', { name: '配置当前模型' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('模型名称', { exact: true }).fill('browser-model');
  await dialog.getByLabel('密钥环境变量名', { exact: true }).fill('invalid key');
  await dialog.getByRole('button', { name: '保存设置', exact: true }).click();
  await expect(dialog).toBeVisible();
  await dialog.getByLabel('密钥环境变量名', { exact: true }).fill('BROWSER_API_KEY');
  await dialog.getByRole('button', { name: '保存设置', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('button', { name: '配置当前模型' })).toContainText('browser-model');
  const state = await (await page.request.get('/api/state', { headers })).json();
  expect(state.settings.api_key_env).toBe('BROWSER_API_KEY');
  expect(state.settings).not.toHaveProperty('api_key');
  expect(page.url()).not.toContain('token=');
});

test('mobile supports approval, navigation, literal user text and stop', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: testInfo.outputPath('mobile-home.png'), fullPage: true, animations: 'disabled' });
  const task = '<img src=x onerror="window.compromised=true">';
  await page.getByRole('textbox', { name: '描述你的任务' }).fill(task);
  await page.getByRole('button', { name: '发送任务', exact: true }).click();
  await expect(page.locator('.message.user .message-text')).toHaveText(task);
  await expect(page.locator('.message.user img')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '批准修改', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '关闭审查面板' }).click();
  await page.screenshot({ path: testInfo.outputPath('mobile.png'), fullPage: true, animations: 'disabled' });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole('button', { name: '停止任务' }).click();
  await expect(page.getByRole('status')).toHaveText('已停止');
  await page.getByRole('button', { name: '打开会话导航' }).click();
  await page.getByRole('button', { name: '新建会话' }).click();
  await expect(page.locator('.home')).toBeVisible();
});

test('stream cancellation marks partial text as unsaved and invalidates approval', async ({ page }) => {
  await page.getByRole('textbox', { name: '描述你的任务' }).fill('等待流式回复');
  await page.getByRole('button', { name: '发送任务', exact: true }).click();
  await expect(page.locator('.message.assistant .message-text')).toContainText('未完成的流式文本');
  await page.getByRole('button', { name: '停止任务' }).click();
  await expect(page.getByRole('status')).toHaveText('已停止');
  await expect(page.getByText('未保存的部分回复', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText('未保存的部分回复', { exact: true })).toBeVisible();
});

test('reference theme keeps gradient, neutral background and reduced motion', async ({ page }, testInfo) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect(page.locator('.home')).toHaveCSS('background-color', 'rgb(12, 12, 12)');
  await expect(page.locator('.wordmark .brand-mark line')).toHaveCount(24);
  await page.getByRole('textbox', { name: '描述你的任务' }).fill('从理解代码开始');
  const gradient = await page.locator('.composer').evaluate(element => getComputedStyle(element, '::after').backgroundImage);
  expect(gradient).toContain('rgb(255, 212, 0)');
  expect(gradient).toContain('rgb(120, 208, 205)');
  const send = page.getByRole('button', { name: '发送任务', exact: true });
  expect(await send.evaluate(element => getComputedStyle(element, '::before').animationName)).toBe('none');
  await page.screenshot({ path: testInfo.outputPath('welcome.png'), fullPage: true, animations: 'disabled' });
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await expect.poll(() => send.evaluate(element => getComputedStyle(element, '::before').animationName)).toBe('send-ring-sweep');
});
