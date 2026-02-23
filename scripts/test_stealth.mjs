import { chromium } from 'playwright-extra';
import stealth from 'puppeteer-extra-plugin-stealth';

chromium.use(stealth());

async function run() {
  const browser = await chromium.launchPersistentContext('/home/openclaw/.openclaw/browser-profile-x', {
    headless: true,
    executablePath: '/home/openclaw/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome',
    args: ['--no-sandbox', '--disable-blink-features=AutomationControlled'],
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
  });

  const page = await browser.newPage();
  await page.goto('https://x.com/login');
  await page.waitForTimeout(5000);
  await page.screenshot({ path: 'stealth_login.png' });
  console.log('URL:', page.url());
  await browser.close();
}

run();
