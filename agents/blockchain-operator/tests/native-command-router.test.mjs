import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveNativeCommand } from '../src/core/native-command-router.mjs';

test('resolve /saldo slash native command to portfolio_balance v2 route', () => {
  const route = resolveNativeCommand('/saldo');

  assert.equal(route.command, 'saldo');
  assert.equal(route.action, 'portfolio_balance');
  assert.equal(route.resolvedInstruction, '/saldo');
  assert.equal(route.route, 'portfolio_balance_v2');
});

test('resolve saldo alias without slash to same route', () => {
  const route = resolveNativeCommand('saldo');

  assert.equal(route.command, 'saldo');
  assert.equal(route.resolvedInstruction, '/saldo');
});

test('unsupported native command is rejected', () => {
  assert.throws(
    () => resolveNativeCommand('/ping'),
    (error) => error?.code === 'NATIVE_COMMAND_UNSUPPORTED'
  );
});
