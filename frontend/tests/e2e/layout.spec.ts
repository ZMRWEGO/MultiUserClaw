import { test, expect } from '@playwright/test';

async function login(page: any) {
  await page.goto('/login');
  await page.fill('#username', 'e2e_tester_2026');
  await page.fill('#password', 'E2ETester123!');
  await page.click('button[type="submit"]');
  await page.waitForURL('/', { timeout: 10000 });
}

test.describe('Layout', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
    await page.waitForSelector('[data-testid="toggle-sidebar"]', { state: 'visible' });
  });

  test('sidebar can be collapsed and expanded', async ({ page }) => {
    const toggle = page.locator('[data-testid="toggle-sidebar"]');
    await expect(toggle).toBeVisible();

    // Left panel should have some width initially (expanded)
    const leftPanel = page.locator('[data-panel]').first();
    const initialWidth = await leftPanel.evaluate((el: HTMLElement) => el.getBoundingClientRect().width);
    expect(initialWidth).toBeGreaterThan(100);

    // Click collapse
    await toggle.click();
    await page.waitForTimeout(400); // allow animation

    const collapsedWidth = await leftPanel.evaluate((el: HTMLElement) => el.getBoundingClientRect().width);
    expect(collapsedWidth).toBeLessThan(80);

    // Click expand
    await toggle.click();
    await page.waitForTimeout(400);

    const expandedWidth = await leftPanel.evaluate((el: HTMLElement) => el.getBoundingClientRect().width);
    expect(expandedWidth).toBeGreaterThan(100);
  });

  test('workspace panel can be collapsed and expanded', async ({ page }) => {
    const toggle = page.locator('[data-testid="toggle-workspace"]');
    await expect(toggle).toBeVisible();

    // Right panel should have some width initially
    const panels = page.locator('[data-panel]');
    const count = await panels.count();
    const rightPanel = panels.nth(count - 1);
    const initialWidth = await rightPanel.evaluate((el: HTMLElement) => el.getBoundingClientRect().width);
    expect(initialWidth).toBeGreaterThan(200);

    // Click collapse
    await toggle.click();
    await page.waitForTimeout(400);

    const collapsedWidth = await rightPanel.evaluate((el: HTMLElement) => el.getBoundingClientRect().width);
    expect(collapsedWidth).toBeLessThan(50);

    // Click expand
    await toggle.click();
    await page.waitForTimeout(400);

    const expandedWidth = await rightPanel.evaluate((el: HTMLElement) => el.getBoundingClientRect().width);
    expect(expandedWidth).toBeGreaterThan(200);
  });

  test('workspace inner panels are horizontal', async ({ page }) => {
    // When workspace is expanded, it should contain FileTree and FilePreview side by side
    const rightPanel = page.locator('[data-panel]').last();
    const rightWidth = await rightPanel.evaluate((el: HTMLElement) => el.getBoundingClientRect().width);
    expect(rightWidth).toBeGreaterThan(200);

    // FileTree should be visible with its "工作区" header
    await expect(page.locator('text=工作区').first()).toBeVisible();

    // FilePreview placeholder should be visible when no file selected
    await expect(page.locator('text=点击左侧文件查看预览')).toBeVisible();
  });
});
