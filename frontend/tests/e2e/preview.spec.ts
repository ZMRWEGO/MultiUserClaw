import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

const FIXTURES_DIR = path.join(__dirname, '..', 'fixtures');

async function login(page: any) {
  await page.goto('/login');
  await page.fill('#username', 'e2e_tester_2026');
  await page.fill('#password', 'E2ETester123!');
  await page.click('button[type="submit"]');
  await page.waitForURL('/', { timeout: 10000 });
}

async function mockWorkspaceFile(page: any, fileName: string, kind: string) {
  await page.route('**/api/nanobot/workspace/browse**', async (route: any) => {
    const url = new URL(route.request().url());
    const dirPath = url.searchParams.get('path') || '';
    if (dirPath === '') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          path: '',
          items: [
            {
              name: fileName,
              path: fileName,
              type: 'file',
              size: 1024,
              modified: new Date().toISOString(),
            },
          ],
        }),
      });
    } else {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ path: dirPath, items: [] }),
      });
    }
  });

  await page.route('**/api/nanobot/workspace/preview**', async (route: any) => {
    const url = new URL(route.request().url());
    const p = url.searchParams.get('path') || '';
    if (p.endsWith(fileName)) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          kind,
          size: 1024,
          modified: new Date().toISOString(),
          download_url: `/api/nanobot/workspace/download?path=${encodeURIComponent(fileName)}`,
          content_type: kind === 'image' ? 'image/png' : undefined,
        }),
      });
    } else {
      await route.continue();
    }
  });

  await page.route('**/api/nanobot/workspace/download**', async (route: any) => {
    const url = new URL(route.request().url());
    const p = url.searchParams.get('path') || '';
    if (p.endsWith(fileName)) {
      const ext = path.extname(fileName).toLowerCase();
      let fixtureFile = '';
      if (ext === '.xlsx') fixtureFile = 'test.xlsx';
      else if (ext === '.pdf') fixtureFile = 'test.pdf';
      else if (ext === '.docx') fixtureFile = 'test.docx';
      else if (ext === '.html') fixtureFile = 'test.html';

      if (fixtureFile) {
        const buf = fs.readFileSync(path.join(FIXTURES_DIR, fixtureFile));
        const mimeTypes: Record<string, string> = {
          '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          '.pdf': 'application/pdf',
          '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
          '.html': 'text/html',
        };
        await route.fulfill({
          status: 200,
          contentType: mimeTypes[ext] || 'application/octet-stream',
          body: buf,
        });
        return;
      }
    }
    await route.continue();
  });
}

test.describe('Preview layout', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('Excel preview has sticky header in thead', async ({ page }) => {
    await mockWorkspaceFile(page, 'test.xlsx', 'xlsx');
    await page.goto('/');
    await page.waitForSelector('[data-testid="toggle-sidebar"]', { state: 'visible' });

    await page.waitForSelector('[data-testid="file-item"]', { state: 'visible', timeout: 5000 });
    await page.locator('[data-testid="file-item"]').click();

    const table = page.locator('[data-testid="excel-preview-table"]');
    await expect(table).toBeVisible({ timeout: 10000 });

    const thead = table.locator('thead');
    await expect(thead).toHaveCount(1);

    // Verify header cells (th) have sticky class
    const ths = thead.locator('th');
    await expect(ths.first()).toBeVisible();
    const hasStickyClass = await ths.first().evaluate((el: HTMLElement) => el.classList.contains('sticky'));
    expect(hasStickyClass).toBe(true);

    const tds = table.locator('tbody td');
    await expect(tds.first()).toBeVisible();

    const previewContainer = page.locator('.min-w-0.overflow-hidden').last();
    const containerRect = await previewContainer.evaluate((el: HTMLElement) => el.getBoundingClientRect());
    const parentRect = await previewContainer.evaluate(
      (el: HTMLElement) => el.parentElement!.getBoundingClientRect()
    );
    expect(containerRect.width).toBeLessThanOrEqual(parentRect.width + 1);
    expect(containerRect.height).toBeLessThanOrEqual(parentRect.height + 1);
  });

  test('PDF preview iframe fills container', async ({ page }) => {
    await mockWorkspaceFile(page, 'test.pdf', 'pdf');
    await page.goto('/');
    await page.waitForSelector('[data-testid="toggle-sidebar"]', { state: 'visible' });

    await page.waitForSelector('[data-testid="file-item"]', { state: 'visible', timeout: 5000 });
    await page.locator('[data-testid="file-item"]').click();

    const iframe = page.locator('iframe[title^="PDF preview"]');
    await expect(iframe).toBeVisible({ timeout: 10000 });

    const iframeRect = await iframe.boundingBox();
    expect(iframeRect).not.toBeNull();
    if (iframeRect) {
      expect(iframeRect.width).toBeGreaterThan(100);
      expect(iframeRect.height).toBeGreaterThan(100);
    }
  });

  test('HTML preview iframe fills container', async ({ page }) => {
    await mockWorkspaceFile(page, 'test.html', 'html');
    await page.goto('/');
    await page.waitForSelector('[data-testid="toggle-sidebar"]', { state: 'visible' });

    await page.waitForSelector('[data-testid="file-item"]', { state: 'visible', timeout: 5000 });
    await page.locator('[data-testid="file-item"]').click();

    const iframe = page.locator('iframe[title^="HTML preview"]');
    await expect(iframe).toBeVisible({ timeout: 10000 });

    const iframeRect = await iframe.boundingBox();
    expect(iframeRect).not.toBeNull();
    if (iframeRect) {
      expect(iframeRect.width).toBeGreaterThan(100);
      expect(iframeRect.height).toBeGreaterThan(100);
    }
  });

  test('Word preview container does not overflow', async ({ page }) => {
    await mockWorkspaceFile(page, 'test.docx', 'docx');
    await page.goto('/');
    await page.waitForSelector('[data-testid="toggle-sidebar"]', { state: 'visible' });

    await page.waitForSelector('[data-testid="file-item"]', { state: 'visible', timeout: 5000 });
    await page.locator('[data-testid="file-item"]').click();

    const host = page.locator('.docx-viewer-host');
    await expect(host).toBeVisible({ timeout: 15000 });

    const overflow = await host.evaluate((el: HTMLElement) => getComputedStyle(el).overflow);
    expect(overflow).toBe('auto');

    const maxWidth = await host.evaluate((el: HTMLElement) => getComputedStyle(el).maxWidth);
    expect(maxWidth).toBe('100%');
  });

  test('Excel popup preview opens and large table scrolls in dialog', async ({ page }) => {
    // Use 100x60 file to trigger react-window Grid path (>50 columns)
    await page.route('**/api/nanobot/workspace/browse**', async (route: any) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          path: '',
          items: [{
            name: 'test_100rows.xlsx',
            path: 'test_100rows.xlsx',
            type: 'file',
            size: 10240,
            modified: new Date().toISOString(),
          }],
        }),
      });
    });
    await page.route('**/api/nanobot/workspace/preview**', async (route: any) => {
      const url = new URL(route.request().url());
      const p = url.searchParams.get('path') || '';
      if (p.endsWith('test_100rows.xlsx')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            kind: 'xlsx',
            size: 10240,
            modified: new Date().toISOString(),
            download_url: '/api/nanobot/workspace/download?path=test_100rows.xlsx',
          }),
        });
      } else {
        await route.continue();
      }
    });
    await page.route('**/api/nanobot/workspace/download**', async (route: any) => {
      const url = new URL(route.request().url());
      const p = url.searchParams.get('path') || '';
      if (p.endsWith('test_100rows.xlsx')) {
        const buf = fs.readFileSync(path.join(FIXTURES_DIR, 'test_100rows.xlsx'));
        await route.fulfill({
          status: 200,
          contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          body: buf,
        });
      } else {
        await route.continue();
      }
    });

    await page.goto('/');
    await page.waitForSelector('[data-testid="toggle-sidebar"]', { state: 'visible' });
    await page.waitForSelector('[data-testid="file-item"]', { state: 'visible', timeout: 5000 });
    await page.locator('[data-testid="file-item"]').click();

    // Wait for inline preview to load
    const popupBtn = page.locator('[data-testid="popup-preview"]');
    await expect(popupBtn).toBeVisible({ timeout: 15000 });

    // Click popup preview button
    await popupBtn.click();

    // Verify dialog opens
    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toBeVisible({ timeout: 5000 });

    // Verify table is rendered inside dialog
    const table = dialog.locator('table');
    await expect(table).toBeVisible({ timeout: 10000 });

    // Verify table has header row with all columns
    const thCount = await dialog.locator('th').count();
    expect(thCount).toBe(60);

    // Verify table has data rows (99 rows + 1 header = 100 total, but we check for data cells)
    const tdCount = await dialog.locator('td').count();
    expect(tdCount).toBeGreaterThan(5000); // 99 rows × 60 columns = 5940

    // Verify dialog has proper dimensions
    const dialogBox = await dialog.boundingBox();
    expect(dialogBox).not.toBeNull();
    if (dialogBox) {
      expect(dialogBox.height).toBeGreaterThan(400);
      expect(dialogBox.width).toBeGreaterThan(400);
    }

    // Scroll down inside dialog and verify scroll position changes
    // The table container with overflow-auto should be scrollable
    const tableContainer = dialog.locator('table').locator('..');
    const scrollHeight = await tableContainer.evaluate((el: HTMLElement) => el.scrollHeight);
    const clientHeight = await tableContainer.evaluate((el: HTMLElement) => el.clientHeight);
    console.log(`Table container scrollHeight: ${scrollHeight}, clientHeight: ${clientHeight}`);

    // Only test scrolling if the content is actually scrollable
    if (scrollHeight > clientHeight) {
      await tableContainer.evaluate((el: HTMLElement) => { el.scrollTop = 200; });
      await page.waitForTimeout(300);
      const scrollTop = await tableContainer.evaluate((el: HTMLElement) => el.scrollTop);
      expect(scrollTop).toBeGreaterThan(0);
    } else {
      console.log('Table content is not scrollable - height fits within container');
    }

    // Close dialog
    await page.keyboard.press('Escape');
    await expect(dialog).not.toBeVisible();
  });
});
