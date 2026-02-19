import crypto from 'node:crypto';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { privateKeyToAccount } from 'viem/accounts';
import { fromRoot } from '../utils/paths.mjs';
import { NonceCoordinator } from '../core/nonce-coordinator.mjs';
import { OperatorError } from '../utils/errors.mjs';

const execFileAsync = promisify(execFile);

const DEFAULT_MAINNET_API_URL = 'https://api.hyperliquid.xyz';
const DEFAULT_SLIPPAGE_BPS = 50;

function normalizePrivateKey(value) {
  if (!value) return null;
  return value.startsWith('0x') ? value : `0x${value}`;
}

function safeJsonParse(value) {
  if (typeof value !== 'string' || !value.trim()) return null;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function asNumber(value, field) {
  const n = Number(value);
  if (!Number.isFinite(n)) {
    throw new OperatorError('HL_NUMBER_INVALID', `${field} inválido`, { field, value });
  }
  return n;
}

function extractStatusErrors(exchangeResponse) {
  const errors = [];

  if (exchangeResponse?.status && exchangeResponse.status !== 'ok') {
    errors.push(String(exchangeResponse.status));
  }

  const statuses = exchangeResponse?.response?.data?.statuses;
  if (Array.isArray(statuses)) {
    for (const status of statuses) {
      if (status && typeof status === 'object' && typeof status.error === 'string') {
        errors.push(status.error);
      }
    }
  }

  return errors;
}

function extractOrderIdentifiers(exchangeResponse) {
  const statuses = exchangeResponse?.response?.data?.statuses;
  if (!Array.isArray(statuses) || statuses.length === 0) {
    return { oid: null };
  }

  for (const status of statuses) {
    if (!status || typeof status !== 'object') continue;

    if (status.resting?.oid != null) {
      return { oid: status.resting.oid };
    }

    if (status.filled?.oid != null) {
      return { oid: status.filled.oid };
    }
  }

  return { oid: null };
}

function deterministicCloid(seed) {
  const hex = crypto.createHash('sha256').update(seed).digest('hex').slice(0, 32);
  return `0x${hex}`;
}

function parsePerpPosition(state, market) {
  const needle = market.toUpperCase();
  const positions = state?.assetPositions ?? [];

  for (const row of positions) {
    const position = row?.position;
    const coin = String(position?.coin ?? '').toUpperCase();
    if (!coin) continue;
    if (coin === needle || coin.endsWith(`:${needle}`)) {
      return position;
    }
  }

  return null;
}

function parseSpotMarket(market) {
  const [base, quote] = market.split('/');
  if (!base || !quote) return null;
  return {
    base: base.toUpperCase(),
    quote: quote.toUpperCase()
  };
}

function maybeMidFromAllMids(mids, market, venue) {
  if (!mids || typeof mids !== 'object') return null;

  const keys = [market, market.toUpperCase()];

  if (venue === 'spot') {
    const split = parseSpotMarket(market);
    if (split) {
      keys.push(`${split.base}/${split.quote}`);
      keys.push(split.base);
      keys.push(`@${split.base}`);
    }
  }

  for (const key of keys) {
    if (key in mids) {
      const value = Number(mids[key]);
      if (Number.isFinite(value) && value > 0) return value;
    }
  }

  return null;
}

function estimateNotional(intent, referencePrice) {
  const amount = Number(intent.amount);
  if (!Number.isFinite(amount) || amount <= 0) return null;

  if (intent.price !== 'market') {
    const px = Number(intent.price);
    return Number.isFinite(px) && px > 0 ? amount * px : null;
  }

  if (referencePrice != null) {
    const px = Number(referencePrice);
    return Number.isFinite(px) && px > 0 ? amount * px : null;
  }

  return null;
}

export class HyperliquidConnector {
  constructor({
    apiUrl,
    accountAddress,
    apiWalletPrivateKey,
    vaultAddress,
    pythonBin,
    bridgeScriptPath,
    bridgeTimeoutMs,
    orderExpiresAfterMs,
    nonceCoordinator
  } = {}) {
    this.apiUrl = apiUrl ?? process.env.HYPERLIQUID_API_URL ?? DEFAULT_MAINNET_API_URL;
    this.accountAddress =
      accountAddress ?? process.env.HYPERLIQUID_ACCOUNT_ADDRESS ?? process.env.HYPERLIQUID_USER_ADDRESS ?? null;
    this.apiWalletPrivateKey = normalizePrivateKey(
      apiWalletPrivateKey ?? process.env.HYPERLIQUID_API_WALLET_PRIVATE_KEY ?? ''
    );
    this.vaultAddress = vaultAddress ?? process.env.HYPERLIQUID_VAULT_ADDRESS ?? null;

    this.pythonBin = pythonBin ?? process.env.HYPERLIQUID_PYTHON_BIN ?? 'python3';
    this.bridgeScriptPath = bridgeScriptPath ?? fromRoot('scripts', 'hyperliquid_live_bridge.py');
    this.bridgeTimeoutMs = Number(bridgeTimeoutMs ?? process.env.HYPERLIQUID_BRIDGE_TIMEOUT_MS ?? 20000);
    this.orderExpiresAfterMs = Number(
      orderExpiresAfterMs ?? process.env.HYPERLIQUID_EXPIRES_AFTER_MS ?? 60000
    );

    this.nonceCoordinator = nonceCoordinator ?? new NonceCoordinator({});
  }

  getSignerAddress() {
    if (!this.apiWalletPrivateKey) return null;

    try {
      return privateKeyToAccount(this.apiWalletPrivateKey).address.toLowerCase();
    } catch (error) {
      throw new OperatorError('HL_API_WALLET_KEY_INVALID', 'HYPERLIQUID_API_WALLET_PRIVATE_KEY inválida', {
        message: error.message
      });
    }
  }

  getAccountAddress() {
    return this.accountAddress;
  }

  hasLiveCredentials() {
    return Boolean(this.getAccountAddress() && this.apiWalletPrivateKey);
  }

  ensureLiveCredentials() {
    if (!this.getAccountAddress()) {
      throw new OperatorError(
        'HL_ACCOUNT_MISSING',
        'HYPERLIQUID_ACCOUNT_ADDRESS não configurado (endereço master/subaccount).'
      );
    }

    if (!this.apiWalletPrivateKey) {
      throw new OperatorError(
        'HL_API_WALLET_KEY_MISSING',
        'HYPERLIQUID_API_WALLET_PRIVATE_KEY não configurada para assinatura live.'
      );
    }
  }

  async post(path, payload) {
    const response = await fetch(`${this.apiUrl}${path}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload)
    });

    const text = await response.text();
    const json = text ? safeJsonParse(text) : null;

    if (!response.ok) {
      throw new OperatorError('HL_HTTP_ERROR', `Hyperliquid erro HTTP ${response.status}`, {
        status: response.status,
        body: json ?? text
      });
    }

    return json ?? text;
  }

  async info(payload) {
    return this.post('/info', payload);
  }

  async exchange(payload) {
    return this.post('/exchange', payload);
  }

  async getAllMids() {
    return this.info({ type: 'allMids' });
  }

  async getMarketReference(intent) {
    const mids = await this.getAllMids();
    const referencePrice = maybeMidFromAllMids(mids, intent.market, intent.venue);

    let source = 'allMids';

    if (referencePrice == null && intent.venue === 'spot') {
      try {
        const spotMetaAndCtxs = await this.info({ type: 'spotMetaAndAssetCtxs' });
        const ctxs = Array.isArray(spotMetaAndCtxs) ? spotMetaAndCtxs[1] : [];
        const split = parseSpotMarket(intent.market);

        if (Array.isArray(ctxs) && split) {
          const match = ctxs.find((ctx) => String(ctx?.coin ?? '').toUpperCase() === split.base);
          const px = Number(match?.midPx ?? match?.markPx ?? NaN);
          if (Number.isFinite(px) && px > 0) {
            return {
              referencePrice: px,
              source: 'spotMetaAndAssetCtxs',
              mids
            };
          }
        }
      } catch {
        // best-effort fallback only
      }
      source = 'unknown';
    }

    return {
      referencePrice,
      source,
      mids
    };
  }

  resolveSlippageBps(intent) {
    if (intent.slippageBps == null) return DEFAULT_SLIPPAGE_BPS;
    return asNumber(intent.slippageBps, 'slippageBps');
  }

  async enrichIntentForPolicy(intent, policy) {
    if (!['hl_order', 'hl_modify'].includes(intent.action)) {
      return intent;
    }

    const enriched = { ...intent };

    if (
      enriched.price === 'market' &&
      enriched.slippageBps == null &&
      policy?.limits?.defaultSlippageBps != null
    ) {
      enriched.slippageBps = String(Number(policy.limits.defaultSlippageBps));
    }

    const needsMarketRef =
      enriched.price === 'market' &&
      (policy?.limits?.maxNotionalUsdPerTx != null || policy?.limits?.maxNotionalUsdPerDay != null);

    if (needsMarketRef && enriched.referencePrice == null) {
      const marketRef = await this.getMarketReference(enriched);
      if (marketRef.referencePrice != null) {
        enriched.referencePrice = String(marketRef.referencePrice);
      }
    }

    return enriched;
  }

  async preflightOrder(intent) {
    const marketRef = await this.getMarketReference(intent);
    const referencePrice =
      intent.referencePrice != null ? Number(intent.referencePrice) : marketRef.referencePrice ?? null;
    const notionalUsd = estimateNotional(intent, referencePrice);

    const preflight = {
      chain: 'hyperliquid',
      action: intent.action,
      market: intent.market,
      venue: intent.venue,
      side: intent.side,
      amount: intent.amount,
      reduceOnly: Boolean(intent.reduceOnly),
      requestedPrice: intent.price,
      referencePrice,
      referenceSource: marketRef.source,
      notionalUsd,
      slippageBps: this.resolveSlippageBps(intent)
    };

    if (intent.price === 'market' && referencePrice == null) {
      throw new OperatorError(
        'HL_PRICE_REFERENCE_UNAVAILABLE',
        'Sem referência de preço para market order em Hyperliquid.',
        { market: intent.market }
      );
    }

    const accountAddress = this.getAccountAddress();
    if (!accountAddress) {
      return {
        ...preflight,
        walletReady: false,
        note: 'HYPERLIQUID_ACCOUNT_ADDRESS ausente: preflight parcial (sem checks de margem/posição).'
      };
    }

    if (intent.venue === 'spot') {
      if (intent.reduceOnly) {
        throw new OperatorError(
          'HL_REDUCE_ONLY_SPOT_UNSUPPORTED',
          'reduce-only não se aplica a spot na Hyperliquid.'
        );
      }

      const spotState = await this.info({ type: 'spotClearinghouseState', user: accountAddress });
      const balances = Array.isArray(spotState?.balances) ? spotState.balances : [];
      const balanceByCoin = new Map(
        balances.map((b) => [String(b.coin ?? '').toUpperCase(), Number(b.total ?? 0) - Number(b.hold ?? 0)])
      );

      const split = parseSpotMarket(intent.market);
      if (!split) {
        throw new OperatorError('HL_SPOT_MARKET_INVALID', 'Market spot deve estar no formato BASE/QUOTE', {
          market: intent.market
        });
      }

      const baseAvailable = balanceByCoin.get(split.base) ?? 0;
      const quoteAvailable = balanceByCoin.get(split.quote) ?? 0;
      const amount = Number(intent.amount);

      let requiredQuote = null;
      if (referencePrice != null) {
        requiredQuote = amount * referencePrice;
      } else if (intent.price !== 'market') {
        requiredQuote = amount * Number(intent.price);
      }

      if (intent.side === 'buy' && requiredQuote != null && quoteAvailable < requiredQuote) {
        throw new OperatorError('HL_SPOT_BALANCE_INSUFFICIENT', 'Saldo spot insuficiente para ordem de compra', {
          quoteAsset: split.quote,
          quoteAvailable,
          requiredQuote
        });
      }

      if (intent.side === 'sell' && baseAvailable < amount) {
        throw new OperatorError('HL_SPOT_BALANCE_INSUFFICIENT', 'Saldo spot insuficiente para ordem de venda', {
          baseAsset: split.base,
          baseAvailable,
          requiredBase: amount
        });
      }

      return {
        ...preflight,
        walletReady: this.hasLiveCredentials(),
        checks: {
          baseAsset: split.base,
          quoteAsset: split.quote,
          baseAvailable,
          quoteAvailable,
          requiredQuote
        }
      };
    }

    const perpState = await this.info({ type: 'clearinghouseState', user: accountAddress });
    const position = parsePerpPosition(perpState, intent.market);

    const accountValue = Number(
      perpState?.crossMarginSummary?.accountValue ?? perpState?.marginSummary?.accountValue ?? 0
    );
    const totalMarginUsed = Number(
      perpState?.crossMarginSummary?.totalMarginUsed ?? perpState?.marginSummary?.totalMarginUsed ?? 0
    );
    const availableMargin = Math.max(0, accountValue - totalMarginUsed);

    const positionSzi = Number(position?.szi ?? 0);

    if (intent.reduceOnly) {
      if (positionSzi === 0) {
        throw new OperatorError('HL_REDUCE_ONLY_NO_POSITION', 'reduce-only requer posição aberta no ativo');
      }

      const isReducingLong = positionSzi > 0 && intent.side === 'sell';
      const isReducingShort = positionSzi < 0 && intent.side === 'buy';
      if (!isReducingLong && !isReducingShort) {
        throw new OperatorError(
          'HL_REDUCE_ONLY_DIRECTION_INVALID',
          'Side incompatível com redução da posição atual',
          { positionSzi, side: intent.side }
        );
      }
    }

    const leverageHint = intent.leverage != null ? Number(intent.leverage) : Number(position?.leverage?.value ?? 1);
    const requiredMargin =
      notionalUsd != null && Number.isFinite(leverageHint) && leverageHint > 0
        ? notionalUsd / leverageHint
        : null;

    if (!intent.reduceOnly && requiredMargin != null && availableMargin < requiredMargin) {
      throw new OperatorError('HL_PERP_MARGIN_INSUFFICIENT', 'Margem insuficiente para ordem perp', {
        availableMargin,
        requiredMargin,
        leverageHint
      });
    }

    return {
      ...preflight,
      walletReady: this.hasLiveCredentials(),
      checks: {
        accountValue,
        totalMarginUsed,
        availableMargin,
        positionSzi,
        leverageHint,
        requiredMargin
      }
    };
  }

  async preflightCancel(intent) {
    const accountAddress = this.getAccountAddress();
    if (!accountAddress) {
      return {
        chain: 'hyperliquid',
        action: 'hl_cancel',
        market: intent.market,
        walletReady: false,
        note: 'HYPERLIQUID_ACCOUNT_ADDRESS ausente: sem validação de open orders.'
      };
    }

    const openOrders = await this.info({ type: 'frontendOpenOrders', user: accountAddress });
    const orders = Array.isArray(openOrders) ? openOrders : [];

    const found = orders.find((order) => {
      const coin = String(order?.coin ?? '').toUpperCase();
      const marketMatch = coin === intent.market.toUpperCase() || coin.endsWith(`:${intent.market.toUpperCase()}`);
      if (!marketMatch) return false;

      if (intent.orderRef.type === 'oid') {
        return Number(order?.oid) === Number(intent.orderRef.value);
      }

      const cloid = order?.cloid ? String(order.cloid).toLowerCase() : null;
      return cloid === String(intent.orderRef.value).toLowerCase();
    });

    if (!found) {
      throw new OperatorError('HL_ORDER_NOT_FOUND', 'Ordem não encontrada para cancelamento', {
        market: intent.market,
        orderRef: intent.orderRef
      });
    }

    return {
      chain: 'hyperliquid',
      action: 'hl_cancel',
      market: intent.market,
      orderRef: intent.orderRef,
      found: {
        oid: found.oid,
        cloid: found.cloid ?? null,
        side: found.side,
        limitPx: found.limitPx,
        sz: found.sz,
        reduceOnly: found.reduceOnly ?? null
      },
      walletReady: this.hasLiveCredentials()
    };
  }

  async preflightModify(intent) {
    const [cancelPreflight, orderPreflight] = await Promise.all([
      this.preflightCancel(intent),
      this.preflightOrder(intent)
    ]);

    return {
      chain: 'hyperliquid',
      action: 'hl_modify',
      market: intent.market,
      orderRef: intent.orderRef,
      walletReady: this.hasLiveCredentials(),
      currentOrder: cancelPreflight.found,
      nextOrder: orderPreflight
    };
  }

  async callBridge(payload) {
    const input = JSON.stringify(payload);

    try {
      const { stdout } = await execFileAsync(this.pythonBin, [this.bridgeScriptPath], {
        input,
        timeout: this.bridgeTimeoutMs,
        maxBuffer: 2 * 1024 * 1024
      });

      const parsed = safeJsonParse(stdout);
      if (!parsed) {
        throw new OperatorError('HL_BRIDGE_OUTPUT_INVALID', 'Resposta inválida da bridge Hyperliquid', {
          stdout
        });
      }

      if (!parsed.ok) {
        throw new OperatorError(
          parsed.error?.code ?? 'HL_BRIDGE_ERROR',
          parsed.error?.message ?? 'Erro na bridge Hyperliquid',
          parsed.error?.details ?? null
        );
      }

      return parsed;
    } catch (error) {
      if (error instanceof OperatorError) {
        throw error;
      }

      if (error.code === 'ENOENT') {
        throw new OperatorError('HL_PYTHON_BIN_NOT_FOUND', 'Python não encontrado para bridge Hyperliquid', {
          pythonBin: this.pythonBin,
          hint: 'Configure HYPERLIQUID_PYTHON_BIN ou instale python3.'
        });
      }

      const parsed = safeJsonParse(error.stdout ?? '');
      if (parsed?.error) {
        throw new OperatorError(
          parsed.error.code ?? 'HL_BRIDGE_ERROR',
          parsed.error.message ?? 'Falha na bridge Hyperliquid',
          parsed.error.details ?? null
        );
      }

      throw new OperatorError('HL_BRIDGE_EXEC_FAILURE', 'Falha ao executar bridge Hyperliquid', {
        message: error.message,
        stderr: (error.stderr ?? '').trim() || null,
        stdout: (error.stdout ?? '').trim() || null
      });
    }
  }

  async nextNonce() {
    const signer = this.getSignerAddress();
    if (!signer) {
      throw new OperatorError(
        'HL_API_WALLET_KEY_MISSING',
        'HYPERLIQUID_API_WALLET_PRIVATE_KEY ausente para geração de nonce'
      );
    }

    return this.nonceCoordinator.nextNonce({ signer });
  }

  async queryOrderStatus({ oid = null, cloid = null } = {}) {
    const accountAddress = this.getAccountAddress();
    if (!accountAddress) return null;

    if (oid == null && cloid == null) return null;

    return this.info({
      type: 'orderStatus',
      user: accountAddress,
      oid: oid ?? cloid
    });
  }

  ensureExchangeAccepted(response) {
    const errors = extractStatusErrors(response);
    if (errors.length > 0) {
      throw new OperatorError('HL_EXCHANGE_REJECTED', 'Hyperliquid rejeitou a ação', {
        errors,
        response
      });
    }
  }


  async preflightDeposit(intent) {
    if (String(intent.asset).toUpperCase() !== 'USDC') {
      throw new OperatorError('HL_DEPOSIT_ASSET_UNSUPPORTED', 'Hyperliquid deposit suporta apenas USDC', {
        asset: intent.asset
      });
    }

    const amount = Number(intent.amount);
    if (!Number.isFinite(amount) || amount <= 0) {
      throw new OperatorError('HL_NUMBER_INVALID', 'amount inválido para deposit', { amount: intent.amount });
    }

    const accountAddress = this.getAccountAddress();
    if (!accountAddress) {
      return {
        chain: 'hyperliquid',
        action: 'hl_deposit',
        asset: 'USDC',
        amount,
        toPerp: intent.toPerp !== false,
        walletReady: false,
        note: 'HYPERLIQUID_ACCOUNT_ADDRESS ausente: preflight parcial.'
      };
    }

    const spotState = await this.info({ type: 'spotClearinghouseState', user: accountAddress });
    const balances = Array.isArray(spotState?.balances) ? spotState.balances : [];
    const usdc = balances.find((row) => String(row?.coin ?? '').toUpperCase() === 'USDC');
    const freeUsdc = usdc ? Number(usdc.total ?? 0) - Number(usdc.hold ?? 0) : 0;

    if (freeUsdc < amount) {
      throw new OperatorError('HL_DEPOSIT_BALANCE_INSUFFICIENT', 'Saldo USDC spot insuficiente para deposit', {
        freeUsdc,
        requestedAmount: amount
      });
    }

    return {
      chain: 'hyperliquid',
      action: 'hl_deposit',
      asset: 'USDC',
      amount,
      toPerp: intent.toPerp !== false,
      walletReady: this.hasLiveCredentials(),
      checks: {
        freeUsdc
      }
    };
  }

  async deposit(intent) {
    this.ensureLiveCredentials();

    const preflight = await this.preflightDeposit(intent);
    const nonce = await this.nextNonce();
    const expiresAfter = this.orderExpiresAfterMs > 0 ? Date.now() + this.orderExpiresAfterMs : null;

    const bridge = await this.callBridge({
      operation: 'deposit',
      apiUrl: this.apiUrl,
      accountAddress: this.getAccountAddress(),
      apiWalletPrivateKey: this.apiWalletPrivateKey,
      vaultAddress: this.vaultAddress,
      nonce,
      expiresAfter,
      deposit: {
        amount: Number(intent.amount),
        toPerp: intent.toPerp !== false
      }
    });

    this.ensureExchangeAccepted(bridge.response);

    return {
      chain: 'hyperliquid',
      action: 'hl_deposit',
      preflight,
      nonce,
      expiresAfter,
      bridge
    };
  }

  async placeOrder(intent, context = {}) {
    this.ensureLiveCredentials();

    const preflight = await this.preflightOrder(intent);
    const nonce = await this.nextNonce();
    const expiresAfter = this.orderExpiresAfterMs > 0 ? Date.now() + this.orderExpiresAfterMs : null;

    const cloid = intent.cloid ?? deterministicCloid(`${context.runId ?? ''}:${context.idempotencyKey ?? ''}:${nonce}`);

    const bridge = await this.callBridge({
      operation: 'order',
      apiUrl: this.apiUrl,
      accountAddress: this.getAccountAddress(),
      apiWalletPrivateKey: this.apiWalletPrivateKey,
      vaultAddress: this.vaultAddress,
      nonce,
      expiresAfter,
      order: {
        market: intent.market,
        side: intent.side,
        size: Number(intent.amount),
        price: intent.price,
        slippageBps: this.resolveSlippageBps(intent),
        referencePrice: preflight.referencePrice,
        reduceOnly: Boolean(intent.reduceOnly),
        tif: intent.price === 'market' ? 'Ioc' : intent.tif ?? 'Gtc',
        cloid
      }
    });

    this.ensureExchangeAccepted(bridge.response);

    const identifiers = extractOrderIdentifiers(bridge.response);
    const orderStatus = await this.queryOrderStatus({ oid: identifiers.oid, cloid });

    return {
      chain: 'hyperliquid',
      action: 'hl_order',
      preflight,
      nonce,
      expiresAfter,
      cloid,
      bridge,
      orderStatus
    };
  }

  async cancelOrder(intent) {
    this.ensureLiveCredentials();

    const preflight = await this.preflightCancel(intent);
    const nonce = await this.nextNonce();
    const expiresAfter = this.orderExpiresAfterMs > 0 ? Date.now() + this.orderExpiresAfterMs : null;

    const bridge = await this.callBridge({
      operation: 'cancel',
      apiUrl: this.apiUrl,
      accountAddress: this.getAccountAddress(),
      apiWalletPrivateKey: this.apiWalletPrivateKey,
      vaultAddress: this.vaultAddress,
      nonce,
      expiresAfter,
      cancel: {
        market: intent.market,
        oid: intent.orderRef.type === 'oid' ? Number(intent.orderRef.value) : null,
        cloid: intent.orderRef.type === 'cloid' ? String(intent.orderRef.value) : null
      }
    });

    this.ensureExchangeAccepted(bridge.response);

    const oid = intent.orderRef.type === 'oid' ? Number(intent.orderRef.value) : preflight.found?.oid ?? null;
    const cloid = intent.orderRef.type === 'cloid' ? String(intent.orderRef.value) : preflight.found?.cloid ?? null;
    const orderStatus = await this.queryOrderStatus({ oid, cloid });

    return {
      chain: 'hyperliquid',
      action: 'hl_cancel',
      preflight,
      nonce,
      expiresAfter,
      bridge,
      orderStatus
    };
  }

  async modifyOrder(intent) {
    this.ensureLiveCredentials();

    const preflight = await this.preflightModify(intent);
    const nonce = await this.nextNonce();
    const expiresAfter = this.orderExpiresAfterMs > 0 ? Date.now() + this.orderExpiresAfterMs : null;

    const cloid =
      intent.cloid ?? deterministicCloid(`modify:${intent.market}:${contextualOrderRef(intent.orderRef)}:${nonce}`);

    const bridge = await this.callBridge({
      operation: 'modify',
      apiUrl: this.apiUrl,
      accountAddress: this.getAccountAddress(),
      apiWalletPrivateKey: this.apiWalletPrivateKey,
      vaultAddress: this.vaultAddress,
      nonce,
      expiresAfter,
      modify: {
        oid: intent.orderRef.type === 'oid' ? Number(intent.orderRef.value) : null,
        cloid: intent.orderRef.type === 'cloid' ? String(intent.orderRef.value) : null,
        order: {
          market: intent.market,
          side: intent.side,
          size: Number(intent.amount),
          price: intent.price,
          slippageBps: this.resolveSlippageBps(intent),
          referencePrice: preflight.nextOrder.referencePrice,
          reduceOnly: Boolean(intent.reduceOnly),
          tif: intent.price === 'market' ? 'Ioc' : intent.tif ?? 'Gtc',
          cloid
        }
      }
    });

    this.ensureExchangeAccepted(bridge.response);

    const orderStatus = await this.queryOrderStatus({
      oid: intent.orderRef.type === 'oid' ? Number(intent.orderRef.value) : null,
      cloid: intent.orderRef.type === 'cloid' ? String(intent.orderRef.value) : null
    });

    return {
      chain: 'hyperliquid',
      action: 'hl_modify',
      preflight,
      nonce,
      expiresAfter,
      bridge,
      orderStatus,
      cloid
    };
  }
}

function contextualOrderRef(orderRef) {
  if (!orderRef) return 'unknown';
  return `${orderRef.type}:${orderRef.value}`;
}
