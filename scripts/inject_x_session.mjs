import { chromium } from 'playwright-extra';
import stealth from 'puppeteer-extra-plugin-stealth';
import fs from 'fs';

chromium.use(stealth());

const AUTH_TOKEN = '41996ea2d1d66566e1309822f8e350455fa55f3f';
const CT0 = '8bbf16e2940db7542e590a4df04b9f58de2ea729e460c28f469e4f40805c7f3d335a950b89461812b13e5f15861bba84e926d387506f8089aeceb531211a0c087980a3810c0b690f695b43120902921e';

async function run() {
  const profilePath = '/home/openclaw/.openclaw/browser-profile-x';
  
  // Launch browser to initialize profile if needed
  const browser = await chromium.launchPersistentContext(profilePath, {
    headless: true,
    executablePath: '/home/openclaw/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome',
    args: ['--no-sandbox', '--disable-blink-features=AutomationControlled'],
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
  });

  const cookies = [
    { name: 'auth_token', value: AUTH_TOKEN, domain: '.x.com', path: '/', secure: true, httpOnly: true, sameSite: 'None' },
    { name: 'ct0', value: CT0, domain: '.x.com', path: '/', secure: true, httpOnly: false, sameSite: 'Lax' }
  ];

  await browser.addCookies(cookies);
  console.log('Cookies injected.');

  const page = await browser.newPage();
  await page.goto('https://x.com/home');
  await page.waitForTimeout(10000);
  
  const url = page.url();
  console.log('Final URL:', url);
  await page.screenshot({ path: 'x_persistent_check.png' });
  
  if (url.includes('/home')) {
    console.log('SUCCESS: Persistent session is active.');
  } else {
    console.log('FAILED: Still redirected to login.');
  }

  await browser.close();
}

run().catch(err => {
  console.error(err);
  process.exit(1);
});
