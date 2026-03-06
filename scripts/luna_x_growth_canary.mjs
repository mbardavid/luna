#!/usr/bin/env node

import fs from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-extra';
import stealth from 'puppeteer-extra-plugin-stealth';

chromium.use(stealth());

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const WORKSPACE = path.resolve(__dirname, '..');
const OUTPUT_DIR = path.join(WORKSPACE, 'artifacts', 'reports', 'luna-x-growth');
const PROFILE_PATH = process.env.LUNA_X_PROFILE_PATH || '/home/openclaw/.openclaw/browser-profile-x';
const CHROME_PATH = process.env.LUNA_X_CHROME_PATH || '/home/openclaw/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome';
const USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';
const DEFAULT_TIMEOUT_MS = 60000;
const AUTH_TOKEN = process.env.AUTH_TOKEN || '';
const CT0 = process.env.CT0 || '';
const HAS_RUNTIME_SESSION = Boolean(AUTH_TOKEN && CT0);

function toIso() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
}

function parseArgs(argv) {
  const args = { _: [] };
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (!token.startsWith('--')) {
      args._.push(token);
      continue;
    }
    const key = token.slice(2);
    const next = argv[index + 1];
    if (!next || next.startsWith('--')) {
      args[key] = true;
      continue;
    }
    args[key] = next;
    index += 1;
  }
  return args;
}

function ensureArg(args, name) {
  const value = args[name];
  if (!value) {
    throw new Error(`missing required argument --${name}`);
  }
  return String(value);
}

function compactNumberToInt(rawValue) {
  const value = String(rawValue || '').trim().replace(/,/g, '');
  if (!value) {
    return 0;
  }
  const match = value.match(/(-?\d+(?:\.\d+)?)([KMB])?/i);
  if (!match) {
    return 0;
  }
  const base = Number.parseFloat(match[1]);
  const suffix = (match[2] || '').toUpperCase();
  const multiplier = suffix === 'K' ? 1000 : suffix === 'M' ? 1000000 : suffix === 'B' ? 1000000000 : 1;
  return Math.round(base * multiplier);
}

function guessFormat(text) {
  const normalized = String(text || '').trim();
  if (!normalized) {
    return 'empty';
  }
  if (/^replying to/i.test(normalized)) {
    return 'reply';
  }
  if (/https?:\/\//i.test(normalized)) {
    return 'link_post';
  }
  if (normalized.split(/\n+/).length >= 4 || normalized.length > 300) {
    return 'thread_or_long_post';
  }
  if (normalized.endsWith('?')) {
    return 'question_post';
  }
  return 'short_post';
}

function extractThemes(posts) {
  const stopWords = new Set([
    'about', 'after', 'again', 'against', 'because', 'before', 'being', 'build', 'built', 'cannot', 'could', 'crypto',
    'from', 'have', 'just', 'market', 'markets', 'more', 'openclaw', 'that', 'there', 'these', 'this', 'those', 'with',
    'will', 'would', 'your', 'into', 'than', 'them', 'they', 'what', 'when', 'where', 'while', 'were', 'been', 'only',
    'very', 'such', 'then', 'also', 'much', 'some', 'over', 'under', 'still', 'like', 'dont', 'cant', 'should', 'their',
  ]);
  const counts = new Map();
  for (const post of posts) {
    const text = String(post.text || '').toLowerCase().replace(/https?:\/\/\S+/g, ' ');
    for (const token of text.split(/[^a-z0-9_]+/)) {
      if (token.length < 4 || stopWords.has(token)) {
        continue;
      }
      counts.set(token, (counts.get(token) || 0) + 1);
    }
  }
  return [...counts.entries()]
    .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
    .slice(0, 5)
    .map(([token]) => token);
}

function formatCounts(posts) {
  const counts = {};
  for (const post of posts) {
    const key = String(post.format || 'unknown');
    counts[key] = (counts[key] || 0) + 1;
  }
  return counts;
}

async function ensureDir(filePath) {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
}

async function writeArtifacts(outputPath, payload, heading) {
  await ensureDir(outputPath);
  await fs.writeFile(outputPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
  const markdownPath = outputPath.replace(/\.json$/i, '.md');
  const account = payload.account || {};
  const profile = payload.profile || {};
  const analytics = payload.analytics || {};
  const lines = [
    `# ${heading}`,
    `Generated: ${payload.generated_at || toIso()}`,
    '',
    '## Session',
    `- state: \`${payload.session_state || 'unknown'}\``,
    `- home_url: ${payload.home_url || '(n/a)'}`,
    `- profile_url: ${account.profile_url || payload.profile_url || '(n/a)'}`,
    '',
    '## Account',
    `- handle: \`${account.handle || '(unknown)'}\``,
    `- display_name: ${account.display_name || '(unknown)'}`,
    `- followers: ${profile.followers ?? '(unknown)'}`,
    `- following: ${profile.following ?? '(unknown)'}`,
    '',
    '## Analytics',
    `- profile_visits_recent: ${analytics.profile_visits_recent ?? '(unavailable)'}`,
    `- impressions_recent: ${analytics.impressions_recent ?? '(unavailable)'}`,
    '',
    '## Themes',
    `- ${(payload.recent_themes || []).join(', ') || '(none)'}`,
  ];
  await fs.writeFile(markdownPath, `${lines.join('\n')}\n`, 'utf8');
}

async function launchContext() {
  const launchOptions = {
    headless: true,
    executablePath: CHROME_PATH,
    args: ['--no-sandbox', '--disable-blink-features=AutomationControlled'],
  };

  if (HAS_RUNTIME_SESSION) {
    const browser = await chromium.launch(launchOptions);
    const context = await browser.newContext({ userAgent: USER_AGENT });
    await context.addCookies([
      { name: 'auth_token', value: AUTH_TOKEN, domain: '.x.com', path: '/', secure: true, httpOnly: true, sameSite: 'None' },
      { name: 'ct0', value: CT0, domain: '.x.com', path: '/', secure: true, httpOnly: false, sameSite: 'Lax' },
      { name: 'auth_token', value: AUTH_TOKEN, domain: 'x.com', path: '/', secure: true, httpOnly: true, sameSite: 'None' },
      { name: 'ct0', value: CT0, domain: 'x.com', path: '/', secure: true, httpOnly: false, sameSite: 'Lax' },
    ]);
    return {
      context,
      close: async () => {
        await context.close();
        await browser.close();
      },
    };
  }

  const context = await chromium.launchPersistentContext(PROFILE_PATH, {
    ...launchOptions,
    userAgent: USER_AGENT,
  });
  return {
    context,
    close: async () => {
      await context.close();
    },
  };
}

async function firstVisibleText(page, selectors) {
  for (const selector of selectors) {
    try {
      const locator = page.locator(selector).first();
      if (await locator.count() && await locator.isVisible({ timeout: 1000 })) {
        const value = await locator.textContent();
        if (value && value.trim()) {
          return value.trim();
        }
      }
    } catch {
      // Best effort.
    }
  }
  return '';
}

async function discoverProfileUrl(page) {
  const directSelectors = [
    'a[data-testid="AppTabBar_Profile_Link"]',
    'a[aria-label*="Profile"]',
  ];
  for (const selector of directSelectors) {
    try {
      const locator = page.locator(selector).first();
      if (await locator.count()) {
        const href = await locator.getAttribute('href');
        if (href) {
          return new URL(href, 'https://x.com').toString();
        }
      }
    } catch {
      // Continue.
    }
  }
  return page.evaluate(() => {
    const anchors = Array.from(document.querySelectorAll('a[href]'));
    for (const anchor of anchors) {
      const href = anchor.getAttribute('href') || '';
      if (!/^\/[A-Za-z0-9_]{1,15}$/.test(href)) {
        continue;
      }
      const text = (anchor.textContent || '').toLowerCase();
      const aria = (anchor.getAttribute('aria-label') || '').toLowerCase();
      if (text.includes('profile') || aria.includes('profile')) {
        return new URL(href, 'https://x.com').toString();
      }
    }
    return '';
  });
}

async function sessionProbe(context) {
  const page = await context.newPage();
  await page.goto('https://x.com/home', { waitUntil: 'domcontentloaded', timeout: DEFAULT_TIMEOUT_MS });
  await page.waitForTimeout(4000);
  const homeUrl = page.url();
  const sessionState = homeUrl.includes('/i/flow/login') ? 'login_required' : 'ok';
  const title = await page.title();
  const profileUrl = sessionState === 'ok' ? await discoverProfileUrl(page) : '';
  await page.close();
  return {
    generated_at: toIso(),
    session_state: sessionState,
    home_url: homeUrl,
    title,
    profile_url: profileUrl,
  };
}

async function metricFromAria(card, selector) {
  try {
    const locator = card.locator(selector).first();
    if (!await locator.count()) {
      return 0;
    }
    const label = String(await locator.getAttribute('aria-label') || '').trim();
    const match = label.match(/(\d[\d.,KMB]*)/i);
    return match ? compactNumberToInt(match[1]) : 0;
  } catch {
    return 0;
  }
}

async function extractPosts(page) {
  const posts = [];
  const cards = page.locator('[data-testid="tweet"]');
  const count = Math.min(await cards.count(), 5);
  for (let index = 0; index < count; index += 1) {
    const card = cards.nth(index);
    const text = String(await card.innerText()).trim();
    const statusLink = card.locator('a[href*="/status/"]').first();
    const url = await statusLink.getAttribute('href').catch(() => '');
    posts.push({
      rank: index + 1,
      text,
      format: guessFormat(text),
      url: url ? new URL(url, 'https://x.com').toString() : '',
      metrics: {
        replies: await metricFromAria(card, '[data-testid="reply"]'),
        reposts: await metricFromAria(card, '[data-testid="retweet"]'),
        likes: await metricFromAria(card, '[data-testid="like"]'),
        views: await metricFromAria(card, 'a[href*="/analytics"]'),
      },
    });
  }
  return posts;
}

function parseLabeledMetric(text, label) {
  const pattern = new RegExp(`([\\d.,KMB]+)\\s+${label}`, 'i');
  const match = String(text || '').match(pattern);
  return match ? compactNumberToInt(match[1]) : null;
}

async function tryAnalytics(context, handle) {
  if (!handle) {
    return { available: false, profile_visits_recent: null, impressions_recent: null };
  }
  const page = await context.newPage();
  try {
    await page.goto(`https://x.com/${handle}/analytics`, { waitUntil: 'domcontentloaded', timeout: DEFAULT_TIMEOUT_MS });
    await page.waitForTimeout(4000);
    if (page.url().includes('/i/flow/login')) {
      return { available: false, profile_visits_recent: null, impressions_recent: null };
    }
    const body = await page.locator('body').innerText();
    return {
      available: true,
      profile_visits_recent: parseLabeledMetric(body, 'Profile visits'),
      impressions_recent: parseLabeledMetric(body, 'Impressions'),
    };
  } catch {
    return { available: false, profile_visits_recent: null, impressions_recent: null };
  } finally {
    await page.close();
  }
}

async function captureSnapshot(commandArgs, mode) {
  const runtime = await launchContext();
  try {
    const session = await sessionProbe(runtime.context);
    const outputPath = path.resolve(String(commandArgs.output || path.join(OUTPUT_DIR, mode === 'baseline' ? 'baseline-latest.json' : 'profile-snapshot-latest.json')));
    if (session.session_state !== 'ok') {
      const payload = {
        ...session,
        mode,
        account: {},
        profile: {},
        analytics: {},
        recent_posts: [],
        recent_themes: [],
        recent_formats: {},
      };
      await writeArtifacts(outputPath, payload, mode === 'baseline' ? 'Luna X Baseline' : 'Luna X Profile Snapshot');
      return { payload, exitCode: 2, outputPath };
    }

    const profileUrl = String(commandArgs['profile-url'] || process.env.LUNA_X_PROFILE_URL || session.profile_url || '').trim();
    if (!profileUrl) {
      throw new Error('unable to determine Luna profile url from active session');
    }

    const page = await runtime.context.newPage();
    await page.goto(profileUrl, { waitUntil: 'domcontentloaded', timeout: DEFAULT_TIMEOUT_MS });
    await page.waitForTimeout(4000);
    const currentUrl = page.url();
    const handle = new URL(currentUrl).pathname.split('/').filter(Boolean)[0] || '';
    const displayName = await firstVisibleText(page, ['div[data-testid="UserName"] span', 'h2[role="heading"] span']);
    const followersText = await firstVisibleText(page, ['a[href$="/verified_followers"]', 'a[href$="/followers"]']);
    const followingText = await firstVisibleText(page, ['a[href$="/following"]']);
    const posts = await extractPosts(page);
    const analytics = await tryAnalytics(runtime.context, handle);
    await page.close();

    const payload = {
      ...session,
      mode,
      account: {
        handle: handle ? `@${handle}` : '',
        handle_raw: handle,
        display_name: displayName,
        profile_url: currentUrl,
      },
      profile: {
        followers: compactNumberToInt(followersText),
        following: compactNumberToInt(followingText),
      },
      analytics,
      recent_posts: posts,
      recent_themes: extractThemes(posts),
      recent_formats: formatCounts(posts),
    };

    await writeArtifacts(outputPath, payload, mode === 'baseline' ? 'Luna X Baseline' : 'Luna X Profile Snapshot');
    if (mode === 'baseline') {
      const snapshotPath = path.join(path.dirname(outputPath), 'profile-snapshot-latest.json');
      await writeArtifacts(snapshotPath, payload, 'Luna X Profile Snapshot');
    }
    return { payload, exitCode: 0, outputPath };
  } finally {
    await runtime.close();
  }
}

async function runHealth(commandArgs) {
  const runtime = await launchContext();
  try {
    const payload = await sessionProbe(runtime.context);
    const outputPath = path.resolve(String(commandArgs.output || path.join(OUTPUT_DIR, 'session-health-latest.json')));
    await writeArtifacts(outputPath, payload, 'Luna X Session Health');
    return { payload, exitCode: 0, outputPath };
  } finally {
    await runtime.close();
  }
}

async function runPublish(commandArgs) {
  const body = commandArgs.body
    ? String(commandArgs.body)
    : await fs.readFile(path.resolve(ensureArg(commandArgs, 'body-file')), 'utf8');
  const confirmLive = Boolean(commandArgs['confirm-live']);
  const outputPath = path.resolve(String(commandArgs.output || path.join(OUTPUT_DIR, 'publish-proof-latest.json')));
  const runtime = await launchContext();
  try {
    const session = await sessionProbe(runtime.context);
    if (session.session_state !== 'ok') {
      const payload = { ...session, action: 'publish', result: 'login_required', body_preview: body.slice(0, 120) };
      await writeArtifacts(outputPath, payload, 'Luna X Publish Proof');
      return { payload, exitCode: 2 };
    }

    const page = await runtime.context.newPage();
    await page.goto('https://x.com/compose/post', { waitUntil: 'domcontentloaded', timeout: DEFAULT_TIMEOUT_MS });
    await page.waitForTimeout(4000);
    const textbox = page.locator('div[data-testid="tweetTextarea_0"]').first();
    await textbox.click();
    await page.keyboard.insertText(body);
    const screenshotPath = outputPath.replace(/\.json$/i, confirmLive ? '-posted.png' : '-draft.png');
    await page.screenshot({ path: screenshotPath, fullPage: true });

    let result = 'draft_ready';
    if (confirmLive) {
      const button = page.locator('button[data-testid="tweetButtonInline"]').first();
      await button.click();
      await page.waitForTimeout(5000);
      result = 'posted';
    }
    await page.close();
    const payload = {
      ...session,
      action: 'publish',
      result,
      body_preview: body.slice(0, 280),
      confirm_live: confirmLive,
      screenshot_path: screenshotPath,
    };
    await writeArtifacts(outputPath, payload, 'Luna X Publish Proof');
    return { payload, exitCode: 0 };
  } finally {
    await runtime.close();
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const command = String(args._[0] || '').trim();
  if (!command || ['help', '--help', '-h'].includes(command)) {
    console.log(`usage: luna_x_growth_canary.mjs <health|snapshot|baseline|publish> [--output path] [--profile-url url]\nAUTH_TOKEN and CT0 env vars enable stateless authenticated runs.`);
    process.exit(0);
  }

  let result;
  if (command === 'health') {
    result = await runHealth(args);
  } else if (command === 'snapshot') {
    result = await captureSnapshot(args, 'snapshot');
  } else if (command === 'baseline') {
    result = await captureSnapshot(args, 'baseline');
  } else if (command === 'publish') {
    result = await runPublish(args);
  } else {
    throw new Error(`unsupported command: ${command}`);
  }

  console.log(result.outputPath || '');
  process.exit(result.exitCode || 0);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack || error.message : String(error));
  process.exit(1);
});
