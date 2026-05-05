import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import * as crypto from 'crypto';
// @ts-ignore - unzipper has runtime types via @types/unzipper but its
// surface uses CommonJS interop that TS sometimes flags depending on tsconfig.
import unzipper from 'unzipper';

// Headed browser, per project requirement: the spec must run in a real
// window so we can visually verify the spinner + toast.
test.use({ headless: false });

const E2E_USER = 'e2e_tester_2026';
const E2E_PASS = 'E2ETester123!';
const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8080';

// Directory we provision under the workspace for this run; suffixed with a
// random hex so concurrent runs don't collide.
const RUN_ID = crypto.randomBytes(4).toString('hex');
const TEST_DIR = `e2e_dl_${RUN_ID}`;

interface PreparedFile {
  name: string;
  size: number;
}

const FILES: PreparedFile[] = [
  { name: 'small.txt', size: 64 },
  { name: 'medium.bin', size: 16 * 1024 },
  { name: 'large.bin', size: 110 * 1024 }, // > 100KB per spec
];

async function login(page: Page) {
  await page.goto('/login');
  await page.fill('#username', E2E_USER);
  await page.fill('#password', E2E_PASS);
  await page.click('button[type="submit"]');
  await page.waitForURL('/', { timeout: 15000 });
}

async function authedRequest(request: APIRequestContext): Promise<string> {
  const r = await request.post(`${API_BASE}/api/auth/login`, {
    data: { username: E2E_USER, password: E2E_PASS },
  });
  if (!r.ok()) throw new Error(`login failed: ${r.status()} ${await r.text()}`);
  const body = await r.json();
  return body.access_token as string;
}

async function provisionWorkspace(request: APIRequestContext, token: string) {
  // Create the directory.
  const mk = await request.post(
    `${API_BASE}/api/nanobot/workspace/mkdir?path=${encodeURIComponent(TEST_DIR)}`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  if (!mk.ok()) {
    throw new Error(`mkdir failed: ${mk.status()} ${await mk.text()}`);
  }

  // Upload three files; one >100KB random bytes.
  for (const f of FILES) {
    const buf = crypto.randomBytes(f.size);
    const upload = await request.post(`${API_BASE}/api/nanobot/workspace/upload`, {
      headers: { Authorization: `Bearer ${token}` },
      multipart: {
        path: TEST_DIR,
        file: { name: f.name, mimeType: 'application/octet-stream', buffer: buf },
      },
    });
    if (!upload.ok()) {
      throw new Error(`upload ${f.name} failed: ${upload.status()} ${await upload.text()}`);
    }
  }
}

async function cleanupWorkspace(request: APIRequestContext, token: string) {
  await request
    .delete(
      `${API_BASE}/api/nanobot/workspace/delete?path=${encodeURIComponent(TEST_DIR)}`,
      { headers: { Authorization: `Bearer ${token}` } },
    )
    .catch(() => {
      // best-effort cleanup; the next run uses a different RUN_ID anyway.
    });
}

test.describe('Workspace folder download (zip)', () => {
  let token = '';

  test.beforeAll(async ({ request }) => {
    token = await authedRequest(request);
    await provisionWorkspace(request, token);
  });

  test.afterAll(async ({ request }) => {
    await cleanupWorkspace(request, token);
  });

  test('downloads directory as zip with loading feedback', async ({ page }) => {
    await login(page);
    await page.waitForSelector('[data-testid="toggle-sidebar"]', { state: 'visible' });

    // Refresh tree to make sure our newly provisioned dir shows up.
    await page.locator('[data-testid="refresh-tree"]').click();

    // Locate the test directory row.
    const dirRow = page.locator(`[data-path="${TEST_DIR}"][data-testid="dir-item"]`);
    await expect(dirRow).toBeVisible({ timeout: 10000 });

    // Hover so the trailing download button becomes visible.
    await dirRow.hover();

    // Listen for the browser download event before clicking.
    const downloadPromise = page.waitForEvent('download', { timeout: 60000 });

    const dlBtn = dirRow.locator('[data-testid="download-dir"]');
    await dlBtn.click();

    // Loading indicators must appear (spinner replaces icon, toast pops up).
    await expect(dirRow.locator('.animate-spin')).toBeVisible({ timeout: 5000 });
    await expect(page.locator('[data-sonner-toast]').filter({ hasText: '正在压缩' })).toBeVisible({
      timeout: 5000,
    });

    // Wait for the download to land.
    const download = await downloadPromise;
    const suggested = download.suggestedFilename();
    expect(suggested).toMatch(/\.zip$/);
    expect(suggested).toContain(TEST_DIR);

    const savedPath = await download.path();
    expect(savedPath).toBeTruthy();
    if (!savedPath) throw new Error('download has no saved path');
    const stat = fs.statSync(savedPath);
    expect(stat.size).toBeGreaterThan(0);

    // Validate the zip and assert all 3 prepared files are present.
    const dir = await unzipper.Open.file(savedPath);
    const entryNames = dir.files.map((f: { path: string }) => f.path);
    for (const f of FILES) {
      expect(entryNames).toContain(f.name);
    }

    // After completion the spinner reverts and the success toast replaces the
    // loading one.
    await expect(dirRow.locator('.animate-spin')).toHaveCount(0, { timeout: 10000 });
  });

  test('file download still works without compression toast', async ({ page }) => {
    await login(page);
    await page.waitForSelector('[data-testid="toggle-sidebar"]', { state: 'visible' });
    await page.locator('[data-testid="refresh-tree"]').click();

    // Expand the test directory so file rows render.
    const dirRow = page.locator(`[data-path="${TEST_DIR}"][data-testid="dir-item"]`);
    await expect(dirRow).toBeVisible({ timeout: 10000 });
    await dirRow.locator('button').first().click(); // expands

    const fileRow = page.locator(
      `[data-path="${TEST_DIR}/small.txt"][data-testid="file-item"]`,
    );
    await expect(fileRow).toBeVisible({ timeout: 10000 });

    // Make sure no archive toast is visible before we click.
    await expect(
      page.locator('[data-sonner-toast]').filter({ hasText: '正在压缩' }),
    ).toHaveCount(0);

    const downloadPromise = page.waitForEvent('download', { timeout: 30000 });
    await fileRow.hover();
    await fileRow.locator('[data-testid="download-file"]').click();

    const download = await downloadPromise;
    const suggested = download.suggestedFilename();
    expect(suggested).toBe('small.txt');
    expect(suggested).not.toMatch(/\.zip$/);

    // Compression toast must NOT appear on the file path.
    await expect(
      page.locator('[data-sonner-toast]').filter({ hasText: '正在压缩' }),
    ).toHaveCount(0);
  });
});
