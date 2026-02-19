import test from 'node:test';
import assert from 'node:assert/strict';
import {
  ConsolidatedBalanceService,
  formatConsolidatedBalanceForDiscord
} from '../src/core/portfolio-balance.mjs';

function makeRow(overrides = {}) {
  return {
    asset: 'ASSET',
    quantity: 0,
    valuationKind: 'spot',
    priceSymbol: null,
    entryPriceUsd: null,
    bucket: 'spot',
    fixedPriceUsd: null,
    fixedValueUsd: null,
    notes: [],
    priceUsd: null,
    priceSource: null,
    priceUpdatedAt: null,
    valueUsd: null,
    ...overrides
  };
}

function makeWallet({ network, walletAddress, rows, errors = [], warnings = [], meta = {} }) {
  return {
    network,
    walletAddress,
    rows,
    errors,
    warnings,
    meta,
    subtotalUsd: null,
    subtotalHint: null
  };
}

test('aggregates balances with mark-to-market and hyperliquid equity subtotal', async () => {
  const service = new ConsolidatedBalanceService({
    collectors: {
      base: async () =>
        makeWallet({
          network: 'base',
          walletAddress: '0x000000000000000000000000000000000000dEaD',
          rows: [
            makeRow({ asset: 'ETH', quantity: 1, valuationKind: 'spot', priceSymbol: 'ETH', bucket: 'spot' }),
            makeRow({ asset: 'USDC', quantity: 100, valuationKind: 'spot', priceSymbol: 'USDC', bucket: 'spot' })
          ]
        }),
      solana: async () =>
        makeWallet({
          network: 'solana',
          walletAddress: '11111111111111111111111111111111',
          rows: [makeRow({ asset: 'SOL', quantity: 2, valuationKind: 'spot', priceSymbol: 'SOL', bucket: 'spot' })]
        }),
      hyperliquid: async () =>
        makeWallet({
          network: 'hyperliquid',
          walletAddress: '0x1111111111111111111111111111111111111111',
          rows: [
            makeRow({
              asset: 'USD-COLLATERAL',
              quantity: 700,
              valuationKind: 'fixed',
              fixedPriceUsd: 1,
              fixedValueUsd: 700,
              bucket: 'perp'
            }),
            makeRow({ asset: 'USDC', quantity: 50, valuationKind: 'spot', priceSymbol: 'USDC', bucket: 'spot' }),
            makeRow({
              asset: 'BTC-PERP',
              quantity: 0.1,
              valuationKind: 'perp_pnl',
              priceSymbol: 'BTC',
              entryPriceUsd: 60000,
              bucket: 'perp'
            })
          ],
          meta: {
            perpAccountValueUsd: 1200
          }
        })
    },
    priceOracle: {
      async getPricesUsd() {
        return new Map([
          ['ETH', { symbol: 'ETH', priceUsd: 2000, source: 'chainlink', updatedAt: '2026-02-18T04:15:00Z' }],
          ['USDC', { symbol: 'USDC', priceUsd: 1, source: 'chainlink', updatedAt: '2026-02-18T04:15:00Z' }],
          ['SOL', { symbol: 'SOL', priceUsd: 100, source: 'pyth', updatedAt: '2026-02-18T04:15:00Z' }],
          ['BTC', { symbol: 'BTC', priceUsd: 65000, source: 'chainlink', updatedAt: '2026-02-18T04:15:00Z' }]
        ]);
      }
    },
    now: () => new Date('2026-02-18T04:20:00.000Z')
  });

  const snapshot = await service.getSnapshot();

  assert.equal(snapshot.snapshotUtc, '2026-02-18T04:20:00.000Z');
  assert.equal(snapshot.wallets[0].subtotalUsd, 2100);
  assert.equal(snapshot.wallets[1].subtotalUsd, 200);
  assert.equal(snapshot.wallets[2].subtotalUsd, 1250);
  assert.equal(snapshot.totalUsd, 3550);
  assert.equal(snapshot.unpricedAssets.length, 0);
  assert.match(snapshot.discordMessage, /Total USD: \$3,550\.00/);
});

test('marks asset as N/A when no reliable price is available', async () => {
  const service = new ConsolidatedBalanceService({
    collectors: {
      base: async () =>
        makeWallet({
          network: 'base',
          walletAddress: '0x000000000000000000000000000000000000dEaD',
          rows: [makeRow({ asset: 'NEW', quantity: 10, valuationKind: 'spot', priceSymbol: 'NEW', bucket: 'spot' })]
        }),
      solana: async () =>
        makeWallet({
          network: 'solana',
          walletAddress: '11111111111111111111111111111111',
          rows: []
        }),
      hyperliquid: async () =>
        makeWallet({
          network: 'hyperliquid',
          walletAddress: '0x1111111111111111111111111111111111111111',
          rows: [
            makeRow({
              asset: 'USD-COLLATERAL',
              quantity: 300,
              valuationKind: 'fixed',
              fixedPriceUsd: 1,
              fixedValueUsd: 300,
              bucket: 'perp'
            })
          ],
          meta: {
            perpAccountValueUsd: 300
          }
        })
    },
    priceOracle: {
      async getPricesUsd() {
        return new Map();
      }
    },
    now: () => new Date('2026-02-18T04:30:00.000Z')
  });

  const snapshot = await service.getSnapshot();

  assert.equal(snapshot.wallets[0].rows[0].priceUsd, null);
  assert.equal(snapshot.wallets[0].rows[0].valueUsd, null);
  assert.equal(snapshot.unpricedAssets.length, 1);
  assert.match(snapshot.discordMessage, /Ativos sem preço confiável/);
  assert.match(snapshot.discordMessage, /NEW/);
});

test('formatter outputs monospaced holdings table for Discord', () => {
  const text = formatConsolidatedBalanceForDiscord({
    snapshotUtc: '2026-02-18T04:35:00.000Z',
    wallets: [
      {
        network: 'base',
        walletAddress: '0x000000000000000000000000000000000000dEaD',
        rows: [
          {
            asset: 'ETH',
            quantity: 0.5,
            priceUsd: 2000,
            priceSource: 'chainlink',
            valueUsd: 1000
          }
        ],
        subtotalUsd: 1000,
        subtotalHint: null,
        warnings: [],
        errors: []
      }
    ],
    totalUsd: 1000,
    unpricedAssets: [],
    partialFailures: []
  });

  assert.match(text, /Saldo consolidado/);
  assert.match(text, /token\s+\|\s+wallet\s+\|\s+qty\s+\|\s+price_usd\s+\|\s+value_usd\s+\|\s+%/);
  assert.match(text, /ETH\s+\|\s+Base:0x0000\.\.\.dEaD\s+\|\s+0\.5\s+\|\s+2000\.000000\s+\|\s+1000\.00\s+\|\s+100\.00%/);
  assert.match(text, /Total USD: \$1,000\.00/);
});
