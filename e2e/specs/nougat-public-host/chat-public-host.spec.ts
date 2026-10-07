import { expect, test } from '@playwright/test';

for (const hostname of ['chat.juristai.org', 'chat.nougat.law']) {
  test(`${hostname} serves login and keeps OAuth URLs on the current host`, async ({ page }) => {
    const response = await page.goto(`https://${hostname}/`, { waitUntil: 'domcontentloaded' });

    expect(response?.status()).toBe(200);
    await expect(page).toHaveURL(`https://${hostname}/login`);
    await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible();
    await expect(page.getByRole('textbox', { name: 'Email' })).toBeVisible();
    await expect(page.getByRole('textbox', { name: 'Password' })).toBeVisible();

    const config = await page.evaluate(async () => {
      const result = await fetch('/api/config');
      return { status: result.status, body: await result.json() };
    });
    expect(config.status).toBe(200);
    expect(config.body.serverDomain).toBe(`https://${hostname}`);
  });
}
