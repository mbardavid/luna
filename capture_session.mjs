import { chromium } from 'playwright-core';
import fs from 'fs';

async function capture() {
  try {
    const browser = await chromium.connectOverCDP('http://127.0.0.1:18792');
    const contexts = browser.contexts();
    if (contexts.length === 0) {
      console.error('No contexts found');
      process.exit(1);
    }
    const state = await contexts[0].storageState();
    fs.writeFileSync('/home/openclaw/.openclaw/browser/openclaw/gmail-state.json', JSON.stringify(state, null, 2));
    console.log('SUCCESS: Captured state to gmail-state.json');
    
    // Also try to list cookies specifically
    const client = await contexts[0].newCDPSession(contexts[0].pages()[0]);
    const { cookies } = await client.send('Network.getAllCookies');
    fs.writeFileSync('/home/openclaw/.openclaw/browser/openclaw/all-cookies.json', JSON.stringify(cookies, null, 2));
    console.log('SUCCESS: Captured all cookies to all-cookies.json');

    await browser.close();
  } catch (err) {
    console.error('FAILED:', err.message);
    process.exit(1);
  }
}
capture();
