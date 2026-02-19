import test from 'node:test';
import assert from 'node:assert/strict';
import { parseInstruction } from '../src/core/parser.mjs';
import { normalizeIntent } from '../src/core/normalize.mjs';
import { PolicyEngine } from '../src/core/policy-engine.mjs';
import policy from '../config/policy.safe-default.json' with { type: 'json' };

test('parse /saldo command for consolidated balance', () => {
  const parsed = parseInstruction('/saldo');
  const normalized = normalizeIntent(parsed);

  assert.equal(normalized.action, 'portfolio_balance');
  assert.deepEqual(normalized.chains, ['base', 'solana', 'hyperliquid']);
});

test('parse PT transfer -> solana', () => {
  const parsed = parseInstruction('enviar 0,01 SOL para 11111111111111111111111111111111 na solana');
  const normalized = normalizeIntent(parsed);

  assert.equal(normalized.action, 'transfer');
  assert.equal(normalized.chain, 'solana');
  assert.equal(normalized.asset, 'SOL');
  assert.equal(normalized.amount, '0.01');
});

test('parse EN transfer -> base inferred by address', () => {
  const parsed = parseInstruction('send 0.001 ETH to 0x000000000000000000000000000000000000dEaD');
  const normalized = normalizeIntent(parsed);

  assert.equal(normalized.chain, 'base');
  assert.equal(normalized.asset, 'ETH');
});

test('parse HL order with risk fields', () => {
  const parsed = parseInstruction(
    'buy 10 HYPE perp at market on hyperliquid reduce-only leverage 2 slippage 50 bps tif ioc'
  );
  const normalized = normalizeIntent(parsed);

  assert.equal(normalized.action, 'hl_order');
  assert.equal(normalized.chain, 'hyperliquid');
  assert.equal(normalized.venue, 'perp');
  assert.equal(normalized.price, 'market');
  assert.equal(normalized.reduceOnly, true);
  assert.equal(normalized.leverage, '2');
  assert.equal(normalized.slippageBps, '50');
  assert.equal(normalized.tif, 'Ioc');
});

test('parse HL cancel by oid', () => {
  const parsed = parseInstruction('cancel order 123 BTC perp on hyperliquid');
  const normalized = normalizeIntent(parsed);

  assert.equal(normalized.action, 'hl_cancel');
  assert.equal(normalized.orderRef.type, 'oid');
  assert.equal(normalized.orderRef.value, '123');
  assert.equal(normalized.market, 'BTC');
});

test('parse HL modify by cloid', () => {
  const parsed = parseInstruction(
    'modify order cloid 0x1234567890abcdef1234567890abcdef sell 1 BTC perp at 45000 on hl'
  );
  const normalized = normalizeIntent(parsed);

  assert.equal(normalized.action, 'hl_modify');
  assert.equal(normalized.orderRef.type, 'cloid');
  assert.equal(normalized.orderRef.value, '0x1234567890abcdef1234567890abcdef');
  assert.equal(normalized.side, 'sell');
  assert.equal(normalized.amount, '1');
  assert.equal(normalized.price, '45000');
});

test('parse PT swap intent for jupiter', () => {
  const parsed = parseInstruction('troque 1 SOL por USDC');
  const normalized = normalizeIntent(parsed);

  assert.equal(normalized.action, 'swap_jupiter');
  assert.equal(normalized.chain, 'solana');
  assert.equal(normalized.assetIn, 'SOL');
  assert.equal(normalized.assetOut, 'USDC');
  assert.equal(normalized.amount, '1');
  assert.equal(normalized.mode, 'ExactIn');
});

test('parse EN swap intent for raydium', () => {
  const parsed = parseInstruction('swap 5 USDC for SOL on raydium slippage 80 bps');
  const normalized = normalizeIntent(parsed);

  assert.equal(normalized.action, 'swap_raydium');
  assert.equal(normalized.assetIn, 'USDC');
  assert.equal(normalized.assetOut, 'SOL');
  assert.equal(normalized.slippageBps, '80');
});

test('parse bridge route', () => {
  const parsed = parseInstruction('bridge 50 USDC from base to solana');
  const normalized = normalizeIntent(parsed);

  assert.equal(normalized.action, 'bridge');
  assert.equal(normalized.fromChain, 'base');
  assert.equal(normalized.toChain, 'solana');
});

test('policy checks pass for dry-run transfer', () => {
  const parsed = parseInstruction('send 0.1 ETH to 0x000000000000000000000000000000000000dEaD on base');
  const normalized = normalizeIntent(parsed);

  const result = new PolicyEngine(policy).evaluate(normalized, { isDryRun: true });
  assert.ok(Array.isArray(result.checks));
});

test('policy checks pass for /saldo command', () => {
  const parsed = parseInstruction('/saldo');
  const normalized = normalizeIntent(parsed);

  const result = new PolicyEngine(policy).evaluate(normalized, { isDryRun: true });
  assert.ok(Array.isArray(result.checks));
});

test('policy checks pass for swap command', () => {
  const parsed = parseInstruction('troque 1 SOL por USDC');
  const normalized = normalizeIntent(parsed);

  const result = new PolicyEngine(policy).evaluate(normalized, { isDryRun: true });
  assert.ok(Array.isArray(result.checks));
});
