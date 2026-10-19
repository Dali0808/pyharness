import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  use: {
    baseURL: 'http://127.0.0.1:4173',
    browserName: 'chromium',
    channel: process.env.LARIO_BROWSER_CHANNEL,
    viewport: { width: 1440, height: 1000 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'uv run --locked --directory .. python -m test.web_browser_server',
    url: 'http://127.0.0.1:4173',
    reuseExistingServer: false,
  },
});
