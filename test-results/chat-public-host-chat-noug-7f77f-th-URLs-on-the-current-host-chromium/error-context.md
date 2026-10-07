# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: chat-public-host.spec.ts >> chat.nougat.law serves login and keeps OAuth URLs on the current host
- Location: e2e\specs\nougat-public-host\chat-public-host.spec.ts:4:7

# Error details

```
Error: expect(received).toBe(expected) // Object.is equality

Expected: "https://chat.nougat.law"
Received: "https://chat.juristai.org"
```

# Page snapshot

```yaml
- generic [ref=e2]:
  - generic [ref=e3]:
    - img "LibreChat Logo" [ref=e5]
    - generic:
      - generic:
        - generic [ref=e6]:
          - button "Toggle theme" [ref=e7] [cursor=pointer]
          - button "Toggle high contrast" [ref=e10] [cursor=pointer]
        - alert [ref=e13]
    - main [ref=e14]:
      - generic [ref=e15]:
        - heading "Welcome back" [level=1] [ref=e16]
        - form "Login form" [ref=e17]:
          - generic [ref=e19]:
            - textbox "Email" [ref=e20]:
              - /placeholder: " "
            - generic [ref=e21]: Email address
          - generic [ref=e24]:
            - textbox "Password" [ref=e25]:
              - /placeholder: " "
            - generic [ref=e26]: Password
            - button "Show secret" [ref=e27] [cursor=pointer]
          - button "Continue" [ref=e31] [cursor=pointer]
        - paragraph [ref=e32]:
          - text: Don't have an account?
          - link "Sign up" [ref=e33] [cursor=pointer]:
            - /url: /register
    - contentinfo
  - region "Notifications (F8)":
    - list
```

# Test source

```ts
  1  | import { expect, test } from '@playwright/test';
  2  | 
  3  | for (const hostname of ['chat.juristai.org', 'chat.nougat.law']) {
  4  |   test(`${hostname} serves login and keeps OAuth URLs on the current host`, async ({ page }) => {
  5  |     const response = await page.goto(`https://${hostname}/`, { waitUntil: 'domcontentloaded' });
  6  | 
  7  |     expect(response?.status()).toBe(200);
  8  |     await expect(page).toHaveURL(`https://${hostname}/login`);
  9  |     await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible();
  10 |     await expect(page.getByRole('textbox', { name: 'Email' })).toBeVisible();
  11 |     await expect(page.getByRole('textbox', { name: 'Password' })).toBeVisible();
  12 | 
  13 |     const config = await page.evaluate(async () => {
  14 |       const result = await fetch('/api/config');
  15 |       return { status: result.status, body: await result.json() };
  16 |     });
  17 |     expect(config.status).toBe(200);
> 18 |     expect(config.body.serverDomain).toBe(`https://${hostname}`);
     |                                      ^ Error: expect(received).toBe(expected) // Object.is equality
  19 |   });
  20 | }
  21 | 
```