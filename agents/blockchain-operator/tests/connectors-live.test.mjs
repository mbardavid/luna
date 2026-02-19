import test from 'node:test';
import assert from 'node:assert/strict';
import { JupiterConnector } from '../src/connectors/jupiter.mjs';
import { RaydiumConnector } from '../src/connectors/raydium.mjs';
import { PumpfunConnector } from '../src/connectors/pumpfun.mjs';
import { DefiConnector } from '../src/connectors/defi.mjs';
import { DebridgeConnector } from '../src/connectors/debridge.mjs';

function makeJsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {
      get(name) {
        if (String(name).toLowerCase() === 'content-type') return 'application/json';
        return null;
      }
    },
    async text() {
      return JSON.stringify(body);
    },
    async arrayBuffer() {
      return Buffer.from(JSON.stringify(body)).buffer;
    }
  };
}

function withPatchedFetch(fetchImpl, fn) {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = fetchImpl;
  return Promise.resolve()
    .then(fn)
    .finally(() => {
      globalThis.fetch = originalFetch;
    });
}

function fakeSolanaConnector() {
  return {
    getAddress() {
      return '11111111111111111111111111111111';
    },
    ensureWallet() {
      return true;
    },
    ensureRecipientAddress(address) {
      return address;
    },
    async preflightSerializedTransaction({ label }) {
      return { label, ok: true };
    },
    async sendSerializedTransaction({ label }) {
      return { label, txHash: `tx-${label}` };
    }
  };
}

test('jupiter live connector executes quote/build/send flow', async () => {
  await withPatchedFetch(
    async (url, options = {}) => {
      const target = String(url);
      const parsedUrl = new URL(target);

      if (parsedUrl.pathname.endsWith('/quote')) {
        return makeJsonResponse({ inAmount: '5000000', outAmount: '25000000', routePlan: [] });
      }

      if (parsedUrl.pathname.endsWith('/swap') && options.method === 'POST') {
        return makeJsonResponse({ swapTransaction: 'dGVzdA==' });
      }

      throw new Error(`Unexpected fetch in Jupiter test: ${target}`);
    },
    async () => {
      const connector = new JupiterConnector({
        apiUrl: 'https://quote-api.jup.ag/v6',
        solana: fakeSolanaConnector()
      });

      const result = await connector.executeSwap({
        action: 'swap_jupiter',
        assetIn: 'USDC',
        assetOut: 'SOL',
        amount: '5',
        mode: 'ExactIn',
        slippageBps: 80,
        recipient: '11111111111111111111111111111111'
      });

      assert.equal(result.connector, 'jupiter');
      assert.equal(result.execution.txHash, 'tx-jupiter.swap');
      assert.equal(result.quote.requestedAmount, '5');
    }
  );
});

test('raydium live connector executes quote/build/send flow', async () => {
  await withPatchedFetch(
    async (url, options = {}) => {
      const target = String(url);

      if (target.includes('/compute/swap-base-in')) {
        return makeJsonResponse({
          success: true,
          data: {
            inAmount: '1000000',
            outAmount: '200000'
          }
        });
      }

      if (target.includes('/transaction/swap-base-in') && options.method === 'POST') {
        return makeJsonResponse({
          success: true,
          data: [{ transaction: 'dGVzdA==' }]
        });
      }

      throw new Error(`Unexpected fetch in Raydium test: ${target}`);
    },
    async () => {
      const connector = new RaydiumConnector({
        apiUrl: 'https://transaction-v1.raydium.io',
        solana: fakeSolanaConnector()
      });

      const result = await connector.executeSwap({
        action: 'swap_raydium',
        assetIn: 'USDC',
        assetOut: 'SOL',
        amount: '1',
        mode: 'ExactIn',
        slippageBps: 90,
        recipient: '11111111111111111111111111111111'
      });

      assert.equal(result.connector, 'raydium');
      assert.equal(result.txCount, 1);
      assert.equal(result.executions[0].execution.txHash, 'tx-raydium.swap.1');
    }
  );
});

test('pumpfun live connector executes trade/send flow', async () => {
  await withPatchedFetch(
    async (url, options = {}) => {
      const target = String(url);

      if (target.includes('pumpportal.fun') && options.method === 'POST') {
        return makeJsonResponse({ transaction: 'dGVzdA==' });
      }

      throw new Error(`Unexpected fetch in Pump.fun test: ${target}`);
    },
    async () => {
      const connector = new PumpfunConnector({
        tradeApiUrl: 'https://pumpportal.fun/api/trade-local',
        solana: fakeSolanaConnector()
      });

      const result = await connector.executeTrade({
        action: 'swap_pumpfun',
        side: 'buy',
        symbol: 'SAGE',
        mint: '11111111111111111111111111111111',
        amount: '0.1',
        amountType: 'quote',
        slippageBps: 200,
        recipient: '11111111111111111111111111111111'
      });

      assert.equal(result.connector, 'pumpfun');
      assert.equal(result.execution.txHash, 'tx-pumpfun.trade');
      assert.equal(result.side, 'buy');
    }
  );
});

test('defi connector dispatches deposit and withdraw through protocol adapter', async () => {
  const calls = [];

  const mockAdapter = {
    supports(intent) {
      return intent.protocol === 'mock-protocol';
    },
    async preflightDeposit(intent) {
      calls.push(['preflightDeposit', intent.target]);
      return { ok: true, action: 'defi_deposit' };
    },
    async executeDeposit(intent) {
      calls.push(['executeDeposit', intent.target]);
      return { ok: true, action: 'defi_deposit' };
    },
    async preflightWithdraw(intent) {
      calls.push(['preflightWithdraw', intent.target]);
      return { ok: true, action: 'defi_withdraw' };
    },
    async executeWithdraw(intent) {
      calls.push(['executeWithdraw', intent.target]);
      return { ok: true, action: 'defi_withdraw' };
    }
  };

  const connector = new DefiConnector({ adapters: [mockAdapter] });

  const deposit = {
    action: 'defi_deposit',
    chain: 'base',
    protocol: 'mock-protocol',
    target: 'vault-1',
    asset: 'USDC',
    amount: '10'
  };

  const withdraw = {
    action: 'defi_withdraw',
    chain: 'base',
    protocol: 'mock-protocol',
    target: 'vault-1',
    asset: 'USDC',
    amount: '1',
    recipient: '0x000000000000000000000000000000000000dEaD'
  };

  const preflightDeposit = await connector.preflightDeposit(deposit);
  const liveDeposit = await connector.executeDeposit(deposit);
  const preflightWithdraw = await connector.preflightWithdraw(withdraw);
  const liveWithdraw = await connector.executeWithdraw(withdraw);

  assert.equal(preflightDeposit.action, 'defi_deposit');
  assert.equal(liveDeposit.action, 'defi_deposit');
  assert.equal(preflightWithdraw.action, 'defi_withdraw');
  assert.equal(liveWithdraw.action, 'defi_withdraw');
  assert.deepEqual(calls, [
    ['preflightDeposit', 'vault-1'],
    ['executeDeposit', 'vault-1'],
    ['preflightWithdraw', 'vault-1'],
    ['executeWithdraw', 'vault-1']
  ]);
});

test('debridge live connector executes source tx and basic tracking', async () => {
  const base = {
    ensureWallet() {},
    async preflightContractCall() {
      return { ok: true };
    },
    async sendContractCall() {
      return { txHash: '0xabc123', receipt: { status: 'success' } };
    }
  };

  const solana = {
    ensureWallet() {},
    async preflightSerializedTransaction() {
      return { ok: true };
    },
    async sendSerializedTransaction() {
      return { txHash: 'sol-tx-1' };
    }
  };

  await withPatchedFetch(
    async (url) => {
      const target = String(url);

      if (target.includes('/dln/order/create-tx')) {
        return makeJsonResponse({
          tx: {
            to: '0x000000000000000000000000000000000000dEaD',
            data: '0x1234',
            value: '0'
          },
          estimation: { outAmount: '999999' },
          orderId: 'order-123'
        });
      }

      if (target.includes('/dln/order/order-123/status')) {
        return makeJsonResponse({ status: 'fulfilled' });
      }

      throw new Error(`Unexpected fetch in deBridge test: ${target}`);
    },
    async () => {
      const connector = new DebridgeConnector({
        apiUrl: 'https://dln.debridge.finance/v1.0',
        base,
        solana
      });

      const result = await connector.executeBridge({
        action: 'bridge',
        fromChain: 'base',
        toChain: 'solana',
        asset: 'USDC',
        amount: '50',
        recipient: '11111111111111111111111111111111'
      });

      assert.equal(result.connector, 'debridge');
      assert.equal(result.execution.sourceTxHash, '0xabc123');
      assert.equal(result.tracking.completed, true);
    }
  );
});
