import { test, expect, Page } from '@playwright/test';

const TEST_USER = 'e2e_tester_2026';
const TEST_PASS = 'E2ETester123!';

interface MockFile {
  name: string;
  path: string;
  size: number;
  content_type: string;
  modified: string;
  preview_kind: 'text' | 'markdown' | 'json' | 'image' | 'pdf' | 'html' | 'docx' | 'xlsx';
}

const NOW = new Date().toISOString();

const FILES: MockFile[] = [
  {
    name: 'report.md',
    path: 'docs/report.md',
    size: 1024,
    content_type: 'text/markdown',
    modified: NOW,
    preview_kind: 'markdown',
  },
  {
    name: 'replan.md',
    path: 'docs/replan.md',
    size: 512,
    content_type: 'text/markdown',
    modified: NOW,
    preview_kind: 'markdown',
  },
  {
    name: 'replicate.ts',
    path: 'src/api/replicate.ts',
    size: 2048,
    content_type: 'application/typescript',
    modified: NOW,
    preview_kind: 'text',
  },
  {
    name: 'main.py',
    path: 'src/main.py',
    size: 256,
    content_type: 'text/x-python',
    modified: NOW,
    preview_kind: 'text',
  },
  {
    name: 'README.md',
    path: 'README.md',
    size: 1500,
    content_type: 'text/markdown',
    modified: NOW,
    preview_kind: 'markdown',
  },
];

async function login(page: Page) {
  await page.goto('/login');
  await page.fill('#username', TEST_USER);
  await page.fill('#password', TEST_PASS);
  await page.click('button[type="submit"]');
  await page.waitForURL('/', { timeout: 10000 });
}

async function setupMocks(page: Page) {
  // Mock the search endpoint with substring filtering
  await page.route('**/api/nanobot/workspace/files**', async (route) => {
    const url = new URL(route.request().url());
    const q = (url.searchParams.get('q') || '').toLowerCase();
    const limit = parseInt(url.searchParams.get('limit') || '20', 10);

    let items = FILES.slice();
    if (q) {
      items = items.filter(
        (f) => f.name.toLowerCase().includes(q) || f.path.toLowerCase().includes(q),
      );
    }
    const total = items.length;
    items = items.slice(0, Math.min(50, Math.max(1, limit)));
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items, total, truncated: total > items.length }),
    });
  });

  // Mock workspace browse so the FileTree renders deterministic structure
  await page.route('**/api/nanobot/workspace/browse**', async (route) => {
    const url = new URL(route.request().url());
    const dirPath = url.searchParams.get('path') || '';
    const fakeDir = (name: string, path: string) => ({
      name,
      path,
      type: 'directory',
      size: null,
      modified: NOW,
    });
    const fakeFile = (f: MockFile) => ({
      name: f.name,
      path: f.path,
      type: 'file',
      size: f.size,
      content_type: f.content_type,
      modified: f.modified,
    });

    let items: any[] = [];
    if (dirPath === '') {
      items = [
        fakeDir('docs', 'docs'),
        fakeDir('src', 'src'),
        fakeFile(FILES.find((f) => f.path === 'README.md')!),
      ];
    } else if (dirPath === 'docs') {
      items = [
        fakeFile(FILES.find((f) => f.path === 'docs/report.md')!),
        fakeFile(FILES.find((f) => f.path === 'docs/replan.md')!),
      ];
    } else if (dirPath === 'src') {
      items = [
        fakeDir('api', 'src/api'),
        fakeFile(FILES.find((f) => f.path === 'src/main.py')!),
      ];
    } else if (dirPath === 'src/api') {
      items = [fakeFile(FILES.find((f) => f.path === 'src/api/replicate.ts')!)];
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ path: dirPath, items }),
    });
  });

  // Mock preview so right-side panel can render after pick
  await page.route('**/api/nanobot/workspace/preview**', async (route) => {
    const url = new URL(route.request().url());
    const p = url.searchParams.get('path') || '';
    const file = FILES.find((f) => f.path === p);
    if (!file) {
      await route.fulfill({ status: 404, body: 'not found' });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        kind: file.preview_kind,
        content: '# mock preview\n',
        size: file.size,
        truncated: false,
        modified: file.modified,
      }),
    });
  });

  // Stub WS endpoint and other endpoints to avoid auth blocking the chat page
  await page.route('**/api/nanobot/sessions', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    });
  });
  await page.route('**/api/nanobot/sessions/**', async (route) => {
    // getSession(key) returns SessionDetail
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        key: 'web:default',
        messages: [],
        created_at: NOW,
        updated_at: NOW,
      }),
    });
  });
  await page.route('**/api/nanobot/commands**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    });
  });
  await page.route('**/api/nanobot/status**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ workspace: '/tmp/x', model: 'mock' }),
    });
  });
}

async function gotoChat(page: Page) {
  await login(page);
  await setupMocks(page);
  await page.goto('/');
  await page.waitForSelector('textarea', { timeout: 10000 });
}

test.describe('Chat input @-mention picker', () => {
  test('@ triggers popup and lists candidates', async ({ page }) => {
    await gotoChat(page);

    const ta = page.locator('textarea').first();
    await ta.click();
    await ta.fill('');
    await ta.press('@'.charAt(0));
    // Wait for debounced search response
    await expect(page.locator('[data-testid="mention-picker"]')).toBeVisible({ timeout: 5000 });

    // 5 items should appear (no query filter)
    const items = page.locator('[data-testid="mention-item"]');
    await expect(items).toHaveCount(5);
  });

  test('typing query filters candidates', async ({ page }) => {
    await gotoChat(page);
    const ta = page.locator('textarea').first();
    await ta.click();
    await ta.fill('');
    await ta.type('@rep');

    await expect(page.locator('[data-testid="mention-picker"]')).toBeVisible({ timeout: 5000 });
    const items = page.locator('[data-testid="mention-item"]');
    // 'rep' matches: report.md, replan.md, replicate.ts (3 items)
    await expect(items).toHaveCount(3);
    // First item shown is 'report.md'/'replan.md'/'replicate.ts' — order varies by mtime,
    // but all 3 must be present.
    const paths = await items.evaluateAll((els) =>
      els.map((el) => (el as HTMLElement).getAttribute('data-path')),
    );
    expect(paths).toContain('docs/report.md');
    expect(paths).toContain('docs/replan.md');
    expect(paths).toContain('src/api/replicate.ts');
  });

  test('email me@example.com does NOT trigger popup', async ({ page }) => {
    await gotoChat(page);
    const ta = page.locator('textarea').first();
    await ta.click();
    await ta.fill('');
    await ta.type('me@example.com');

    // Popup must not appear
    await page.waitForTimeout(300);
    await expect(page.locator('[data-testid="mention-picker"]')).toHaveCount(0);
  });

  test('Escape closes popup', async ({ page }) => {
    await gotoChat(page);
    const ta = page.locator('textarea').first();
    await ta.click();
    await ta.fill('');
    await ta.type('@rep');
    await expect(page.locator('[data-testid="mention-picker"]')).toBeVisible({ timeout: 5000 });

    await ta.press('Escape');
    await expect(page.locator('[data-testid="mention-picker"]')).toHaveCount(0);
    // Textarea content unchanged
    await expect(ta).toHaveValue('@rep');
  });

  test('Enter selects highlighted item; textarea gets @<path> with trailing space', async ({ page }) => {
    await gotoChat(page);
    const ta = page.locator('textarea').first();
    await ta.click();
    await ta.fill('');
    await ta.type('请看 @repo'); // 'repo' fuzzy matches 'report.md'
    await expect(page.locator('[data-testid="mention-picker"]')).toBeVisible({ timeout: 5000 });
    const items = page.locator('[data-testid="mention-item"]');
    await expect(items.first()).toBeVisible();

    // Get the first item's data-path so we can build expected value robustly
    const firstPath = await items.first().getAttribute('data-path');
    expect(firstPath).not.toBeNull();

    await ta.press('Enter');

    // Popup gone
    await expect(page.locator('[data-testid="mention-picker"]')).toHaveCount(0);

    // Textarea value should be: '请看 @<firstPath> '
    const value = await ta.inputValue();
    expect(value).toBe(`请看 @${firstPath} `);
    expect(value.endsWith(' ')).toBe(true);
  });

  test('ArrowDown changes selection then Tab picks the second item', async ({ page }) => {
    await gotoChat(page);
    const ta = page.locator('textarea').first();
    await ta.click();
    await ta.fill('');
    await ta.type('@rep');
    await expect(page.locator('[data-testid="mention-picker"]')).toBeVisible({ timeout: 5000 });

    const items = page.locator('[data-testid="mention-item"]');
    await expect(items.first()).toBeVisible();
    const secondPath = await items.nth(1).getAttribute('data-path');
    expect(secondPath).not.toBeNull();

    await ta.press('ArrowDown');
    await ta.press('Tab');
    await expect(page.locator('[data-testid="mention-picker"]')).toHaveCount(0);

    const value = await ta.inputValue();
    expect(value).toBe(`@${secondPath} `);
  });

  test('selecting a file switches right-side preview', async ({ page }) => {
    await gotoChat(page);
    const ta = page.locator('textarea').first();
    await ta.click();
    await ta.fill('');
    await ta.type('@report');
    await expect(page.locator('[data-testid="mention-picker"]')).toBeVisible({ timeout: 5000 });
    await expect(page.locator('[data-testid="mention-item"]').first()).toBeVisible();
    await ta.press('Enter');

    // Wait for the right-side preview to update to docs/report.md.
    // The header shows the file name; verify it.
    await expect(page.locator('text=report.md').first()).toBeVisible({ timeout: 5000 });
  });

  test('selecting a deep file expands FileTree parent dirs and highlights node', async ({ page }) => {
    await gotoChat(page);
    const ta = page.locator('textarea').first();
    await ta.click();
    await ta.fill('');
    await ta.type('@replicate');
    await expect(page.locator('[data-testid="mention-picker"]')).toBeVisible({ timeout: 5000 });
    await expect(page.locator('[data-testid="mention-item"]').first()).toBeVisible();
    await ta.press('Enter');

    // The picked file is src/api/replicate.ts.
    // FileTree must auto-expand `src/` and `src/api/` so that
    // [data-path="src/api/replicate.ts"] is rendered.
    const node = page.locator('[data-path="src/api/replicate.ts"]');
    await expect(node).toBeVisible({ timeout: 5000 });

    // Selected file should carry the accent class — verify via classList.
    const isSelected = await node.evaluate((el) =>
      (el as HTMLElement).classList.contains('bg-accent'),
    );
    expect(isSelected).toBe(true);
  });
});
