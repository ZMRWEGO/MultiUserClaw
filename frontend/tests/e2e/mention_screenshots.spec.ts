import { test, expect, Page } from '@playwright/test';
import * as fs from 'fs';

const TEST_USER = 'e2e_tester_2026';
const TEST_PASS = 'E2ETester123!';
const NOW = new Date().toISOString();
const SHOTS_DIR = '/tmp/nanobot_e2e_mention';

if (!fs.existsSync(SHOTS_DIR)) fs.mkdirSync(SHOTS_DIR, { recursive: true });

const FILES = [
  { name: 'report.md', path: 'docs/report.md', size: 1024, content_type: 'text/markdown', modified: NOW, preview_kind: 'markdown' },
  { name: 'replan.md', path: 'docs/replan.md', size: 512, content_type: 'text/markdown', modified: NOW, preview_kind: 'markdown' },
  { name: 'replicate.ts', path: 'src/api/replicate.ts', size: 2048, content_type: 'application/typescript', modified: NOW, preview_kind: 'text' },
  { name: 'main.py', path: 'src/main.py', size: 256, content_type: 'text/x-python', modified: NOW, preview_kind: 'text' },
  { name: 'README.md', path: 'README.md', size: 1500, content_type: 'text/markdown', modified: NOW, preview_kind: 'markdown' },
];

async function login(page: Page) {
  await page.goto('/login');
  await page.fill('#username', TEST_USER);
  await page.fill('#password', TEST_PASS);
  await page.click('button[type="submit"]');
  await page.waitForURL('/', { timeout: 10000 });
}

async function setupMocks(page: Page) {
  await page.route('**/api/nanobot/workspace/files**', async (route) => {
    const url = new URL(route.request().url());
    const q = (url.searchParams.get('q') || '').toLowerCase();
    let items = FILES.slice();
    if (q) items = items.filter((f) => f.name.toLowerCase().includes(q) || f.path.toLowerCase().includes(q));
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ items, total: items.length, truncated: false }),
    });
  });
  await page.route('**/api/nanobot/workspace/browse**', async (route) => {
    const url = new URL(route.request().url());
    const dirPath = url.searchParams.get('path') || '';
    const fakeDir = (name: string, path: string) => ({ name, path, type: 'directory', size: null, modified: NOW });
    const fakeFile = (f: any) => ({ ...f, type: 'file' });
    let items: any[] = [];
    if (dirPath === '') items = [fakeDir('docs', 'docs'), fakeDir('src', 'src'), fakeFile(FILES[4])];
    else if (dirPath === 'docs') items = [fakeFile(FILES[0]), fakeFile(FILES[1])];
    else if (dirPath === 'src') items = [fakeDir('api', 'src/api'), fakeFile(FILES[3])];
    else if (dirPath === 'src/api') items = [fakeFile(FILES[2])];
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ path: dirPath, items }) });
  });
  await page.route('**/api/nanobot/workspace/preview**', async (route) => {
    const url = new URL(route.request().url());
    const p = url.searchParams.get('path') || '';
    const file = FILES.find((f) => f.path === p);
    if (!file) { await route.fulfill({ status: 404, body: 'not found' }); return; }
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ kind: file.preview_kind, content: `# Preview of ${file.path}\n\n这是 mock 预览内容。`, size: file.size, truncated: false, modified: file.modified }),
    });
  });
  await page.route('**/api/nanobot/sessions', async (r) => r.fulfill({ status: 200, contentType: 'application/json', body: '[]' }));
  await page.route('**/api/nanobot/sessions/**', async (r) => r.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ key: 'web:default', messages: [], created_at: NOW, updated_at: NOW }),
  }));
  await page.route('**/api/nanobot/commands**', async (r) => r.fulfill({ status: 200, contentType: 'application/json', body: '[]' }));
  await page.route('**/api/nanobot/status**', async (r) => r.fulfill({ status: 200, contentType: 'application/json', body: '{}' }));
}

test.describe('mention screenshots (archive)', () => {
  test('end-to-end @ flow capture', async ({ page }) => {
    await login(page);
    await setupMocks(page);
    await page.goto('/');
    await page.waitForSelector('textarea', { timeout: 10000 });
    await page.screenshot({ path: `${SHOTS_DIR}/01_initial_layout.png`, fullPage: true });

    const ta = page.locator('textarea').first();
    await ta.click();
    await ta.fill('请看 @');
    await expect(page.locator('[data-testid="mention-picker"]')).toBeVisible({ timeout: 5000 });
    await expect(page.locator('[data-testid="mention-item"]').first()).toBeVisible();
    await page.screenshot({ path: `${SHOTS_DIR}/02_at_triggers_popup.png`, fullPage: true });

    await ta.fill('请看 @rep');
    await expect(page.locator('[data-testid="mention-picker"]')).toBeVisible({ timeout: 5000 });
    await page.waitForTimeout(300);
    await page.screenshot({ path: `${SHOTS_DIR}/03_filtered_by_rep.png`, fullPage: true });

    await ta.press('ArrowDown');
    await page.screenshot({ path: `${SHOTS_DIR}/04_after_arrowdown.png`, fullPage: true });

    await ta.press('Enter');
    // Allow preview to load
    await page.waitForTimeout(1500);
    await page.screenshot({ path: `${SHOTS_DIR}/05_after_enter_committed.png`, fullPage: true });

    // Verify final textarea value
    const value = await ta.inputValue();
    expect(value.startsWith('请看 @')).toBe(true);
    expect(value.endsWith(' ')).toBe(true);

    // Email scenario doesn't trigger
    await ta.click();
    await ta.fill('');
    await ta.type('me@example.com');
    await page.waitForTimeout(300);
    await expect(page.locator('[data-testid="mention-picker"]')).toHaveCount(0);
    await page.screenshot({ path: `${SHOTS_DIR}/06_email_no_popup.png`, fullPage: true });

    // Deep file expands tree
    await ta.click();
    await ta.fill('');
    await ta.type('@replicate');
    await expect(page.locator('[data-testid="mention-picker"]')).toBeVisible({ timeout: 5000 });
    await expect(page.locator('[data-testid="mention-item"]').first()).toBeVisible();
    await ta.press('Enter');
    await page.waitForTimeout(2500);
    const node = page.locator('[data-path="src/api/replicate.ts"]');
    await expect(node).toBeVisible({ timeout: 8000 });
    await page.screenshot({ path: `${SHOTS_DIR}/07_filetree_expanded.png`, fullPage: true });
  });
});
