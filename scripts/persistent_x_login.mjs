import { chromium } from 'playwright-extra';
import stealth from 'puppeteer-extra-plugin-stealth';
import fs from 'fs';

chromium.use(stealth());

async function run() {
  const profilePath = '/home/openclaw/.openclaw/browser-profile-x';
  const browser = await chromium.launchPersistentContext(profilePath, {
    headless: true,
    executablePath: '/home/openclaw/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome',
    args: ['--no-sandbox', '--disable-blink-features=AutomationControlled'],
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
  });

  const page = await browser.newPage();
  
  // Go to X login
  console.log('Navigating to X login...');
  await page.goto('https://x.com/i/flow/login');
  await page.waitForTimeout(5000);
  
  // Take initial screenshot
  await page.screenshot({ path: 'stealth_x_start.png' });
  
  // Look for Google login button
  const googleBtn = page.locator('button:has-text("Sign in with Google")');
  if (await googleBtn.isVisible()) {
    console.log('Clicking Google login button...');
    await googleBtn.click();
    await page.waitForTimeout(5000);
    
    // Check if new tab or popup opened (Google login often does)
    const pages = browser.pages();
    let loginPage = page;
    if (pages.length > 2) { // 0 is about:blank, 1 is X, 2+ are popups
       loginPage = pages[pages.length - 1];
    }
    
    await loginPage.screenshot({ path: 'stealth_google_flow.png' });
    console.log('Login page URL:', loginPage.url());
  } else {
    console.log('Google login button not found.');
  }

  // Keep it open for a bit to allow for manual interaction via screenshot/prompt
  // In a real scenario we'd wait for user input here.
  // For now, let's just close and report.
  await browser.close();
}

run().catch(err => {
  console.error(err);
  process.exit(1);
});
