import { defineConfig } from '@playwright/test';

const baseURL = 'http://127.0.0.1:4287';

export default defineConfig({
  testDir: './tests/browser',
  use: {
    baseURL,
    channel: process.env.RELEASETRACE_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : 'chromium'),
    viewport: { width: 390, height: 844 },
  },
  webServer: {
    command: 'node ./tests/browser/server.cjs',
    url: baseURL,
    env: { HOSTNAME:'127.0.0.1', PORT:'4287', RELEASETRACE_LIVE_TRACKING:'1', BLOB_READ_WRITE_TOKEN:'' },
  },
});
