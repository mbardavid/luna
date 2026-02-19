import { PublicKey, LAMPORTS_PER_SOL } from '@solana/web3.js';
import { formatEther, formatUnits, isAddress } from 'viem';
import { BaseConnector } from '../connectors/base.mjs';
import { SolanaConnector } from '../connectors/solana.mjs';
import { HyperliquidConnector } from '../connectors/hyperliquid.mjs';
import { BASE_TOKENS, SOLANA_TOKENS } from '../connectors/token-registry.mjs';

const ERC20_BALANCE_ABI = [
  {
    type: 'function',
    name: 'balanceOf',
    stateMutability: 'view',
    inputs: [{ name: 'account', type: 'address' }],
    outputs: [{ name: 'balance', type: 'uint256' }]
  },
  {
    type: 'function',
    name: 'decimals',
    stateMutability: 'view',
    inputs: [],
    outputs: [{ name: 'decimals', type: 'uint8' }]
  },
  {
    type: 'function',
    name: 'symbol',
    stateMutability: 'view',
    inputs: [],
    outputs: [{ name: 'symbol', type: 'string' }]
  }
];

const CHAINLINK_AGGREGATOR_ABI = [
  {
    type: 'function',
    name: 'decimals',
    stateMutability: 'view',
    inputs: [],
    outputs: [{ name: 'decimals', type: 'uint8' }]
  },
  {
    type: 'function',
    name: 'description',
    stateMutability: 'view',
    inputs: [],
    outputs: [{ name: 'description', type: 'string' }]
  },
  {
    type: 'function',
    name: 'latestRoundData',
    stateMutability: 'view',
    inputs: [],
    outputs: [
      { name: 'roundId', type: 'uint80' },
      { name: 'answer', type: 'int256' },
      { name: 'startedAt', type: 'uint256' },
      { name: 'updatedAt', type: 'uint256' },
      { name: 'answeredInRound', type: 'uint80' }
    ]
  }
];

const TOKEN_PROGRAM_IDS = [
  new PublicKey('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'),
  new PublicKey('TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb')
];

const PRICE_SYMBOL_ALIASES = Object.freeze({
  WETH: 'ETH',
  WSOL: 'SOL'
});

const DEFAULT_CHAINLINK_FEEDS = Object.freeze({
  ETH: ['0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70'],
  SOL: ['0x975043adBb80fc32276CbF9Bbcfd4A601a12462D'],
  USDC: ['0x7e860098F58bBFC8648a4311b374B1D669a2bc6B'],
  USDT: ['0xE5fa3A4e4208858ADdf2CDb4e12651E89f1f1A70'],
  BTC: ['0x8D9e0911A532e2a3C005667B475E6F9742355f2b']
});

function toFiniteNumber(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function nowIsoUtc() {
  return new Date().toISOString();
}

function compactError(error) {
  if (!error) return 'erro desconhecido';
  const base = String(error.message ?? error).replace(/\s+/g, ' ').trim();
  return base.length > 240 ? `${base.slice(0, 237)}...` : base;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function withRetries(fn, { attempts = 3, delayMs = 150 } = {}) {
  let lastError = null;

  for (let i = 0; i < attempts; i += 1) {
    try {
      return await fn();
    } catch (error) {
      lastError = error;
      if (i < attempts - 1) {
        await sleep(delayMs * (i + 1));
      }
    }
  }

  throw lastError;
}

function normalizePriceSymbol(symbol) {
  const raw = String(symbol ?? '').trim().toUpperCase();
  if (!raw) return null;

  if (raw.endsWith('-PERP')) {
    return raw.replace(/-PERP$/, '');
  }

  if (raw.includes('/')) {
    return raw.split('/')[0];
  }

  return PRICE_SYMBOL_ALIASES[raw] ?? raw;
}

function parseAddressList(source) {
  if (!source) return [];

  const raw = String(source).trim();
  if (!raw) return [];

  try {
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) {
      return parsed.map((v) => String(v).trim()).filter(Boolean);
    }
  } catch {
    // fallback to comma-separated
  }

  return raw
    .split(',')
    .map((v) => v.trim())
    .filter(Boolean);
}

function dedupeBy(values, keyFn) {
  const out = [];
  const seen = new Set();

  for (const value of values) {
    const key = keyFn(value);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(value);
  }

  return out;
}

function shortAddress(value, { head = 6, tail = 4 } = {}) {
  if (!value) return 'N/A';
  const text = String(value);
  if (text.length <= head + tail + 3) return text;
  return `${text.slice(0, head)}...${text.slice(-tail)}`;
}

function formatUsd(value) {
  if (!Number.isFinite(value)) return 'N/A';
  return `$${value.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  })}`;
}

function formatPrice(value) {
  if (!Number.isFinite(value)) return 'N/A';
  const abs = Math.abs(value);
  const maxFractionDigits = abs >= 1000 ? 2 : abs >= 1 ? 4 : 8;
  return `$${value.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: maxFractionDigits
  })}`;
}

function formatQuantity(value) {
  if (!Number.isFinite(value)) return 'N/A';
  const abs = Math.abs(value);
  const maxFractionDigits = abs >= 1000 ? 2 : abs >= 1 ? 6 : 10;
  return value.toLocaleString('en-US', {
    minimumFractionDigits: 0,
    maximumFractionDigits: maxFractionDigits
  });
}

function buildRow({
  asset,
  quantity,
  valuationKind = 'spot',
  priceSymbol = null,
  entryPriceUsd = null,
  bucket = 'spot',
  fixedPriceUsd = null,
  fixedValueUsd = null,
  notes = []
}) {
  return {
    asset,
    quantity,
    valuationKind,
    priceSymbol: priceSymbol ?? normalizePriceSymbol(asset),
    entryPriceUsd,
    bucket,
    fixedPriceUsd,
    fixedValueUsd,
    notes,
    priceUsd: null,
    priceSource: null,
    priceUpdatedAt: null,
    valueUsd: null
  };
}

function parsePythSymbolEntry(item) {
  if (!item || typeof item !== 'object') return null;
  const attrs = item.attributes ?? {};
  const symbol = String(attrs.symbol ?? '');
  const display = String(attrs.display_symbol ?? '');
  const feedId = String(item.id ?? '').trim();

  if (!feedId) return null;

  return {
    id: feedId,
    symbol,
    display
  };
}

export class PriceOracle {
  constructor({
    baseClient,
    chainlinkFeeds = DEFAULT_CHAINLINK_FEEDS,
    pythEndpoint = process.env.PYTH_HERMES_URL ?? 'https://hermes.pyth.network',
    maxPriceAgeMs = Number(process.env.PRICE_MAX_AGE_MS ?? 2 * 24 * 60 * 60 * 1000),
    rpcRetries = Number(process.env.PRICE_RPC_RETRIES ?? 3)
  } = {}) {
    this.baseClient = baseClient;
    this.chainlinkFeeds = chainlinkFeeds;
    this.pythEndpoint = pythEndpoint.replace(/\/+$/, '');
    this.maxPriceAgeMs = maxPriceAgeMs;
    this.rpcRetries = Math.max(1, rpcRetries);

    this.pythFeedIdCache = new Map();
  }

  async getPricesUsd(symbols = []) {
    const normalizedSymbols = dedupeBy(
      symbols
        .map((s) => normalizePriceSymbol(s))
        .filter(Boolean),
      (s) => s
    );

    const output = new Map();
    const unresolved = [];

    for (const symbol of normalizedSymbols) {
      const chainlink = await this.getFromChainlink(symbol);
      if (chainlink?.priceUsd != null) {
        output.set(symbol, chainlink);
      } else {
        unresolved.push(symbol);
      }
    }

    if (unresolved.length > 0) {
      const pythResults = await this.getFromPythBatch(unresolved);
      for (const symbol of unresolved) {
        if (pythResults.has(symbol) && pythResults.get(symbol)?.priceUsd != null) {
          output.set(symbol, pythResults.get(symbol));
        }
      }
    }

    return output;
  }

  async getFromChainlink(symbol) {
    if (!this.baseClient) return null;

    const addresses = this.chainlinkFeeds[symbol] ?? [];
    if (!Array.isArray(addresses) || addresses.length === 0) return null;

    for (const address of addresses) {
      if (!isAddress(address)) continue;

      try {
        const [decimals, description, round] = await withRetries(
          () =>
            Promise.all([
              this.baseClient.readContract({
                address,
                abi: CHAINLINK_AGGREGATOR_ABI,
                functionName: 'decimals'
              }),
              this.baseClient.readContract({
                address,
                abi: CHAINLINK_AGGREGATOR_ABI,
                functionName: 'description'
              }),
              this.baseClient.readContract({
                address,
                abi: CHAINLINK_AGGREGATOR_ABI,
                functionName: 'latestRoundData'
              })
            ]),
          { attempts: this.rpcRetries }
        );

        const answer = Number(round[1]);
        const updatedAtSec = Number(round[3]);

        if (!Number.isFinite(answer) || answer <= 0) continue;
        if (!Number.isFinite(updatedAtSec) || updatedAtSec <= 0) continue;

        const priceUsd = answer / 10 ** Number(decimals);
        const updatedAtMs = updatedAtSec * 1000;

        if (Date.now() - updatedAtMs > this.maxPriceAgeMs) {
          continue;
        }

        return {
          symbol,
          priceUsd,
          source: 'chainlink',
          updatedAt: new Date(updatedAtMs).toISOString(),
          feed: {
            address,
            description: String(description ?? '')
          }
        };
      } catch {
        // tenta próximo feed candidate
      }
    }

    return null;
  }

  async resolvePythFeedId(symbol) {
    if (this.pythFeedIdCache.has(symbol)) {
      return this.pythFeedIdCache.get(symbol);
    }

    const canonical = `Crypto.${symbol}/USD`;
    const url = new URL('/v2/price_feeds', this.pythEndpoint);
    url.searchParams.set('query', canonical);

    try {
      const response = await fetch(url, { method: 'GET' });
      if (!response.ok) {
        this.pythFeedIdCache.set(symbol, null);
        return null;
      }

      const list = await response.json();
      if (!Array.isArray(list)) {
        this.pythFeedIdCache.set(symbol, null);
        return null;
      }

      const parsed = list.map(parsePythSymbolEntry).filter(Boolean);
      const exact =
        parsed.find((row) => row.symbol.toUpperCase() === canonical.toUpperCase()) ??
        parsed.find((row) => row.display.toUpperCase() === `${symbol}/USD`);

      const feedId = exact?.id ?? null;
      this.pythFeedIdCache.set(symbol, feedId);
      return feedId;
    } catch {
      this.pythFeedIdCache.set(symbol, null);
      return null;
    }
  }

  async getFromPythBatch(symbols = []) {
    const result = new Map();

    const pairs = [];
    for (const symbol of symbols) {
      const feedId = await this.resolvePythFeedId(symbol);
      if (feedId) {
        pairs.push({ symbol, feedId });
      }
    }

    if (pairs.length === 0) {
      return result;
    }

    const url = new URL('/v2/updates/price/latest', this.pythEndpoint);
    for (const pair of pairs) {
      url.searchParams.append('ids[]', pair.feedId);
    }
    url.searchParams.set('parsed', 'true');

    try {
      const response = await fetch(url, { method: 'GET' });
      if (!response.ok) return result;

      const payload = await response.json();
      const parsed = Array.isArray(payload?.parsed) ? payload.parsed : [];

      const byId = new Map(
        parsed
          .filter((row) => row && typeof row === 'object' && row.id)
          .map((row) => [String(row.id), row])
      );

      for (const { symbol, feedId } of pairs) {
        const row = byId.get(feedId);
        if (!row) continue;

        const rawPrice = toFiniteNumber(row?.price?.price);
        const expo = toFiniteNumber(row?.price?.expo);
        const publishTime = toFiniteNumber(row?.price?.publish_time);

        if (rawPrice == null || expo == null || publishTime == null) continue;

        const priceUsd = rawPrice * 10 ** expo;
        if (!Number.isFinite(priceUsd) || priceUsd <= 0) continue;

        const updatedAtMs = publishTime * 1000;
        if (Date.now() - updatedAtMs > this.maxPriceAgeMs) continue;

        result.set(symbol, {
          symbol,
          priceUsd,
          source: 'pyth',
          updatedAt: new Date(updatedAtMs).toISOString(),
          feed: {
            id: feedId
          }
        });
      }
    } catch {
      return result;
    }

    return result;
  }
}

function createDefaultCollectors({ baseConnector, solanaConnector, hyperliquidConnector }) {
  return {
    base: () => collectBaseWallet(baseConnector),
    solana: () => collectSolanaWallet(solanaConnector),
    hyperliquid: () => collectHyperliquidWallet(hyperliquidConnector)
  };
}

function baseKnownTokenEntries() {
  return Object.values(BASE_TOKENS)
    .filter((token) => token?.address)
    .map((token) => ({
      symbol: token.symbol,
      address: token.address,
      decimals: token.decimals
    }));
}

async function collectBaseWallet(baseConnector) {
  const walletAddress =
    process.env.BASE_ACCOUNT_ADDRESS?.trim() ||
    process.env.BASE_PUBLIC_ADDRESS?.trim() ||
    baseConnector.getAddress() ||
    null;

  const wallet = {
    network: 'base',
    walletAddress,
    rows: [],
    errors: [],
    warnings: [],
    meta: {}
  };

  if (!walletAddress) {
    wallet.errors.push('Endereço Base não configurado (BASE_ACCOUNT_ADDRESS/BASE_PRIVATE_KEY).');
    return wallet;
  }

  const publicClient = baseConnector.publicClient;

  try {
    const balanceWei = await withRetries(() => publicClient.getBalance({ address: walletAddress }), {
      attempts: 3
    });
    const quantityEth = Number(formatEther(balanceWei));

    if (Number.isFinite(quantityEth) && quantityEth > 0) {
      wallet.rows.push(
        buildRow({
          asset: 'ETH',
          quantity: quantityEth,
          valuationKind: 'spot',
          priceSymbol: 'ETH',
          bucket: 'spot'
        })
      );
    }
  } catch (error) {
    wallet.errors.push(`Falha ao consultar saldo ETH na Base: ${compactError(error)}`);
  }

  const envTokens = parseAddressList(process.env.BASE_BALANCE_TOKEN_ADDRESSES_JSON);
  const defaultTokens = baseKnownTokenEntries();

  const extraTokens = envTokens
    .filter((address) => isAddress(address))
    .map((address) => ({
      symbol: null,
      address,
      decimals: null
    }));

  const tokens = dedupeBy([...defaultTokens, ...extraTokens], (token) => token.address.toLowerCase());

  for (const token of tokens) {
    try {
      let symbol = token.symbol;
      let decimals = token.decimals;

      if (symbol == null || decimals == null) {
        const [chainDecimals, chainSymbol] = await withRetries(
          () =>
            Promise.all([
              publicClient.readContract({
                address: token.address,
                abi: ERC20_BALANCE_ABI,
                functionName: 'decimals'
              }),
              publicClient.readContract({
                address: token.address,
                abi: ERC20_BALANCE_ABI,
                functionName: 'symbol'
              })
            ]),
          { attempts: 2 }
        );

        symbol = String(chainSymbol ?? '').trim().toUpperCase() || `ERC20:${shortAddress(token.address)}`;
        decimals = Number(chainDecimals);
      }

      const rawBalance = await withRetries(
        () =>
          publicClient.readContract({
            address: token.address,
            abi: ERC20_BALANCE_ABI,
            functionName: 'balanceOf',
            args: [walletAddress]
          }),
        { attempts: 2 }
      );

      const quantity = Number(formatUnits(rawBalance, decimals));
      if (!Number.isFinite(quantity) || quantity <= 0) continue;

      wallet.rows.push(
        buildRow({
          asset: symbol,
          quantity,
          valuationKind: 'spot',
          priceSymbol: normalizePriceSymbol(symbol),
          bucket: 'spot'
        })
      );
    } catch (error) {
      const label = token.symbol ?? shortAddress(token.address);
      wallet.warnings.push(`Token Base ${label}: ${compactError(error)}`);
    }
  }

  return wallet;
}

function solanaMintSymbolMap() {
  const map = new Map();

  for (const token of Object.values(SOLANA_TOKENS)) {
    if (!token?.mint) continue;

    // em mint duplicado (SOL/WSOL), prioriza WSOL para contas SPL.
    if (!map.has(token.mint) || token.symbol === 'WSOL') {
      map.set(token.mint, {
        symbol: token.symbol,
        decimals: token.decimals
      });
    }
  }

  return map;
}

async function collectSolanaWallet(solanaConnector) {
  const walletAddress =
    process.env.SOLANA_ACCOUNT_ADDRESS?.trim() ||
    process.env.SOLANA_PUBLIC_ADDRESS?.trim() ||
    solanaConnector.getAddress() ||
    null;

  const wallet = {
    network: 'solana',
    walletAddress,
    rows: [],
    errors: [],
    warnings: [],
    meta: {}
  };

  if (!walletAddress) {
    wallet.errors.push('Endereço Solana não configurado (SOLANA_ACCOUNT_ADDRESS/SOLANA_PRIVATE_KEY_*).');
    return wallet;
  }

  let owner;
  try {
    owner = new PublicKey(walletAddress);
  } catch {
    wallet.errors.push('Endereço Solana inválido para consulta de saldo.');
    return wallet;
  }

  const connection = solanaConnector.connection;

  try {
    const lamports = await withRetries(() => connection.getBalance(owner, 'confirmed'), {
      attempts: 3
    });

    const sol = lamports / LAMPORTS_PER_SOL;
    if (Number.isFinite(sol) && sol > 0) {
      wallet.rows.push(
        buildRow({
          asset: 'SOL',
          quantity: sol,
          valuationKind: 'spot',
          priceSymbol: 'SOL',
          bucket: 'spot'
        })
      );
    }
  } catch (error) {
    wallet.errors.push(`Falha ao consultar saldo SOL: ${compactError(error)}`);
  }

  const byMint = new Map();

  for (const programId of TOKEN_PROGRAM_IDS) {
    try {
      const accounts = await withRetries(
        () => connection.getParsedTokenAccountsByOwner(owner, { programId }, 'confirmed'),
        { attempts: 2 }
      );

      for (const item of accounts.value ?? []) {
        try {
          const parsed = item?.account?.data?.parsed;
          const info = parsed?.info;
          if (!info) continue;

          const mint = String(info.mint ?? '').trim();
          const tokenAmount = info.tokenAmount ?? {};
          const rawAmount = String(tokenAmount.amount ?? '0');
          const decimals = Number(tokenAmount.decimals ?? 0);

          const atomic = BigInt(rawAmount);
          if (atomic <= 0n) continue;

          const quantity = Number(formatUnits(atomic, decimals));
          if (!Number.isFinite(quantity) || quantity <= 0) continue;

          const current = byMint.get(mint) ?? { mint, quantity: 0, decimals };
          current.quantity += quantity;
          byMint.set(mint, current);
        } catch {
          // mantém snapshot resiliente em caso de conta SPL malformada
        }
      }
    } catch (error) {
      wallet.warnings.push(`Falha ao ler SPL (${programId.toBase58()}): ${compactError(error)}`);
    }
  }

  const knownByMint = solanaMintSymbolMap();

  for (const token of byMint.values()) {
    const known = knownByMint.get(token.mint);
    const symbol = known?.symbol ?? `SPL:${shortAddress(token.mint, { head: 4, tail: 4 })}`;

    wallet.rows.push(
      buildRow({
        asset: symbol,
        quantity: token.quantity,
        valuationKind: 'spot',
        priceSymbol: normalizePriceSymbol(symbol),
        bucket: 'spot'
      })
    );
  }

  return wallet;
}

function parseHyperliquidAccountValue(state) {
  const cross = toFiniteNumber(state?.crossMarginSummary?.accountValue);
  if (cross != null) return cross;

  const margin = toFiniteNumber(state?.marginSummary?.accountValue);
  if (margin != null) return margin;

  return null;
}

async function collectHyperliquidWallet(hyperliquidConnector) {
  const walletAddress = hyperliquidConnector.getAccountAddress();

  const wallet = {
    network: 'hyperliquid',
    walletAddress,
    rows: [],
    errors: [],
    warnings: [],
    meta: {
      perpAccountValueUsd: null
    }
  };

  if (!walletAddress) {
    wallet.errors.push('HYPERLIQUID_ACCOUNT_ADDRESS não configurado para snapshot de saldo.');
    return wallet;
  }

  const [perpStateResult, spotStateResult] = await Promise.allSettled([
    hyperliquidConnector.info({ type: 'clearinghouseState', user: walletAddress }),
    hyperliquidConnector.info({ type: 'spotClearinghouseState', user: walletAddress })
  ]);

  if (spotStateResult.status === 'fulfilled') {
    const balances = Array.isArray(spotStateResult.value?.balances) ? spotStateResult.value.balances : [];

    for (const balance of balances) {
      const coin = String(balance?.coin ?? '').trim().toUpperCase();
      if (!coin) continue;

      const total = toFiniteNumber(balance?.total);
      if (total == null || total <= 0) continue;

      wallet.rows.push(
        buildRow({
          asset: coin,
          quantity: total,
          valuationKind: 'spot',
          priceSymbol: normalizePriceSymbol(coin),
          bucket: 'spot'
        })
      );
    }
  } else {
    wallet.errors.push(`Falha ao consultar spot Hyperliquid: ${compactError(spotStateResult.reason)}`);
  }

  if (perpStateResult.status === 'fulfilled') {
    const state = perpStateResult.value;
    const accountValue = parseHyperliquidAccountValue(state);
    wallet.meta.perpAccountValueUsd = accountValue;

    const positions = Array.isArray(state?.assetPositions) ? state.assetPositions : [];
    let reportedPnlSum = 0;

    for (const row of positions) {
      const position = row?.position ?? row;
      const coin = String(position?.coin ?? '').trim().toUpperCase();
      if (!coin) continue;

      const size = toFiniteNumber(position?.szi);
      if (size == null || size === 0) continue;

      const entryPrice = toFiniteNumber(position?.entryPx ?? position?.entryPrice);
      const reportedPnl = toFiniteNumber(position?.unrealizedPnl);
      if (reportedPnl != null) {
        reportedPnlSum += reportedPnl;
      }

      wallet.rows.push(
        buildRow({
          asset: `${coin}-PERP`,
          quantity: size,
          valuationKind: 'perp_pnl',
          priceSymbol: normalizePriceSymbol(coin),
          entryPriceUsd: entryPrice,
          bucket: 'perp',
          notes: entryPrice == null ? ['entry_price_missing'] : []
        })
      );
    }

    if (accountValue != null) {
      const collateralUsd = accountValue - reportedPnlSum;
      wallet.rows.unshift(
        buildRow({
          asset: 'USD-COLLATERAL',
          quantity: collateralUsd,
          valuationKind: 'fixed',
          fixedPriceUsd: 1,
          fixedValueUsd: collateralUsd,
          bucket: 'perp'
        })
      );
    }
  } else {
    wallet.errors.push(`Falha ao consultar perp Hyperliquid: ${compactError(perpStateResult.reason)}`);
  }

  return wallet;
}

function shouldRequestPrice(row) {
  if (!row) return false;
  if (row.valuationKind === 'fixed') return false;
  return Boolean(row.priceSymbol);
}

function applyValuation(wallets, pricesBySymbol) {
  const unpricedAssets = [];

  for (const wallet of wallets) {
    for (const row of wallet.rows) {
      if (row.valuationKind === 'fixed') {
        row.priceUsd = row.fixedPriceUsd;
        row.priceSource = 'fixed';
        row.priceUpdatedAt = null;
        row.valueUsd = row.fixedValueUsd;
        continue;
      }

      const price = pricesBySymbol.get(row.priceSymbol);
      if (!price || !Number.isFinite(price.priceUsd)) {
        row.priceUsd = null;
        row.priceSource = null;
        row.priceUpdatedAt = null;
        row.valueUsd = null;
        unpricedAssets.push({
          network: wallet.network,
          asset: row.asset,
          quantity: row.quantity
        });
        continue;
      }

      row.priceUsd = price.priceUsd;
      row.priceSource = price.source;
      row.priceUpdatedAt = price.updatedAt ?? null;

      if (row.valuationKind === 'spot') {
        row.valueUsd = row.quantity * row.priceUsd;
        continue;
      }

      if (row.valuationKind === 'perp_pnl') {
        if (Number.isFinite(row.entryPriceUsd)) {
          row.valueUsd = (row.priceUsd - row.entryPriceUsd) * row.quantity;
        } else {
          row.valueUsd = null;
          unpricedAssets.push({
            network: wallet.network,
            asset: row.asset,
            quantity: row.quantity
          });
        }
      }
    }

    const sumRows = wallet.rows.reduce((acc, row) => {
      if (!Number.isFinite(row.valueUsd)) return acc;
      return acc + row.valueUsd;
    }, 0);

    if (wallet.network === 'hyperliquid' && Number.isFinite(wallet.meta?.perpAccountValueUsd)) {
      const spotSubtotal = wallet.rows.reduce((acc, row) => {
        if (row.bucket !== 'spot') return acc;
        if (!Number.isFinite(row.valueUsd)) return acc;
        return acc + row.valueUsd;
      }, 0);

      wallet.subtotalUsd = spotSubtotal + wallet.meta.perpAccountValueUsd;
      wallet.subtotalHint = 'spot mark-to-market + perp equity';
    } else {
      wallet.subtotalUsd = sumRows;
      wallet.subtotalHint = null;
    }
  }

  return unpricedAssets;
}

function summarizeErrors(wallets) {
  const failures = [];

  for (const wallet of wallets) {
    for (const error of wallet.errors ?? []) {
      failures.push(`${wallet.network}: ${error}`);
    }
  }

  return failures;
}

function networkLabel(network) {
  if (network === 'base') return 'Base';
  if (network === 'solana') return 'Solana';
  if (network === 'hyperliquid') return 'Hyperliquid';
  return network;
}

export function formatConsolidatedBalanceForDiscord(snapshot) {
  const lines = [];

  const tableRows = [];
  for (const wallet of snapshot.wallets) {
    const walletLabel = `${networkLabel(wallet.network)}:${shortAddress(wallet.walletAddress, { head: 6, tail: 4 })}`;

    for (const row of wallet.rows ?? []) {
      tableRows.push({
        token: row.asset,
        wallet: walletLabel,
        qty: Number.isFinite(row.quantity) ? formatQuantity(row.quantity) : 'N/A',
        priceUsd: Number.isFinite(row.priceUsd) ? row.priceUsd.toFixed(6) : 'N/A',
        valueUsdNumber: Number.isFinite(row.valueUsd) ? row.valueUsd : null
      });
    }
  }

  const displayedTotalUsd = tableRows.reduce((acc, row) => {
    if (!Number.isFinite(row.valueUsdNumber)) return acc;
    return acc + row.valueUsdNumber;
  }, 0);

  const displayRows = tableRows.map((row) => {
    const pct = Number.isFinite(row.valueUsdNumber) && displayedTotalUsd > 0
      ? `${((row.valueUsdNumber / displayedTotalUsd) * 100).toFixed(2)}%`
      : 'N/A';

    return {
      token: row.token,
      wallet: row.wallet,
      qty: row.qty,
      priceUsd: row.priceUsd,
      valueUsd: Number.isFinite(row.valueUsdNumber) ? row.valueUsdNumber.toFixed(2) : 'N/A',
      pct
    };
  });

  const headerLine = 'token | wallet | qty | price_usd | value_usd | %';
  const divider = '----- | ------ | --- | --------- | --------- | -';
  const rowLines = displayRows.map(
    (row) => `${row.token} | ${row.wallet} | ${row.qty} | ${row.priceUsd} | ${row.valueUsd} | ${row.pct}`
  );

  lines.push(`📊 Saldo consolidado — snapshot UTC ${snapshot.snapshotUtc}`);
  lines.push('```');
  lines.push(headerLine);
  lines.push(divider);
  lines.push(...(rowLines.length > 0 ? rowLines : ['(sem holdings)']));
  lines.push('```');
  lines.push(`Total USD: ${formatUsd(displayedTotalUsd)}`);

  if (snapshot.unpricedAssets.length > 0) {
    lines.push('');
    lines.push('⚠️ Ativos sem preço confiável (Chainlink/Pyth):');

    for (const row of snapshot.unpricedAssets) {
      lines.push(`- ${networkLabel(row.network)} | ${row.asset} | qtd ${formatQuantity(row.quantity)}`);
    }
  }

  if (snapshot.partialFailures.length > 0) {
    lines.push('');
    lines.push('⚠️ Falhas parciais detectadas:');
    for (const failure of snapshot.partialFailures) {
      lines.push(`- ${failure}`);
    }
  }

  return lines.join('\n').trim();
}

export class ConsolidatedBalanceService {
  constructor({ baseConnector, solanaConnector, hyperliquidConnector, priceOracle, collectors, now } = {}) {
    this.baseConnector = baseConnector ?? new BaseConnector({});
    this.solanaConnector = solanaConnector ?? new SolanaConnector({});
    this.hyperliquidConnector = hyperliquidConnector ?? new HyperliquidConnector({});

    this.collectors = {
      ...createDefaultCollectors({
        baseConnector: this.baseConnector,
        solanaConnector: this.solanaConnector,
        hyperliquidConnector: this.hyperliquidConnector
      }),
      ...(collectors ?? {})
    };

    this.priceOracle =
      priceOracle ??
      new PriceOracle({
        baseClient: this.baseConnector.publicClient
      });

    this.now = typeof now === 'function' ? now : () => new Date();
  }

  async getSnapshot() {
    const snapshotUtc = this.now().toISOString();

    const settled = await Promise.allSettled([
      this.collectors.base(),
      this.collectors.solana(),
      this.collectors.hyperliquid()
    ]);

    const walletFallback = (network, reason) => ({
      network,
      walletAddress: null,
      rows: [],
      errors: [`Falha interna ao montar snapshot ${network}: ${compactError(reason)}`],
      warnings: [],
      meta: {},
      subtotalUsd: null,
      subtotalHint: null
    });

    const wallets = [
      settled[0].status === 'fulfilled' ? settled[0].value : walletFallback('base', settled[0].reason),
      settled[1].status === 'fulfilled' ? settled[1].value : walletFallback('solana', settled[1].reason),
      settled[2].status === 'fulfilled' ? settled[2].value : walletFallback('hyperliquid', settled[2].reason)
    ];

    const symbols = dedupeBy(
      wallets
        .flatMap((wallet) => wallet.rows)
        .filter((row) => shouldRequestPrice(row))
        .map((row) => row.priceSymbol)
        .filter(Boolean),
      (symbol) => symbol
    );

    const pricesBySymbol = await this.priceOracle.getPricesUsd(symbols);
    const unpricedAssets = applyValuation(wallets, pricesBySymbol);

    const totalUsd = wallets.reduce((acc, wallet) => {
      if (!Number.isFinite(wallet.subtotalUsd)) return acc;
      return acc + wallet.subtotalUsd;
    }, 0);

    const partialFailures = summarizeErrors(wallets);

    const snapshot = {
      snapshotUtc,
      wallets,
      totalUsd,
      unpricedAssets,
      partialFailures,
      marketData: {
        primary: 'chainlink',
        fallback: 'pyth'
      }
    };

    return {
      ...snapshot,
      discordMessage: formatConsolidatedBalanceForDiscord(snapshot),
      generatedAtUtc: nowIsoUtc()
    };
  }
}
