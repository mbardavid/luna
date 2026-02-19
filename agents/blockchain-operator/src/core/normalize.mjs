import { CHAIN_ALIASES } from './constants.mjs';
import { OperatorError } from '../utils/errors.mjs';

const EVM_ADDRESS = /^0x[a-fA-F0-9]{40}$/;
const SOLANA_ADDRESS = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
const CLOID = /^0x[a-fA-F0-9]{32}$/;

function normalizeChain(chain) {
  if (!chain) return null;
  const canonical = CHAIN_ALIASES[chain.toLowerCase()];
  if (!canonical) {
    throw new OperatorError('CHAIN_UNSUPPORTED', `Chain não suportada: ${chain}`);
  }
  return canonical;
}

function normalizeAsset(asset) {
  return asset.toUpperCase();
}

function normalizeMarketSymbol(market) {
  return market.toUpperCase();
}

function assertPositiveNumberString(value, field) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new OperatorError('INVALID_AMOUNT', `${field} deve ser número positivo`, { value });
  }
  return String(parsed);
}

function assertNonNegativeNumberString(value, field) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < 0) {
    throw new OperatorError('INVALID_NUMBER', `${field} deve ser número não-negativo`, { value });
  }
  return String(parsed);
}

function inferChainFromAddress(address) {
  if (EVM_ADDRESS.test(address)) return 'base';
  if (SOLANA_ADDRESS.test(address)) return 'solana';
  return null;
}

function normalizeHyperliquidOrderLike(intent, base, { includeOrderRef = false } = {}) {
  const side = intent.side;
  const venue = intent.venue;

  if (!['buy', 'sell'].includes(side)) {
    throw new OperatorError('ORDER_SIDE_INVALID', `Side inválido: ${side}`);
  }

  if (!['spot', 'perp'].includes(venue)) {
    throw new OperatorError('ORDER_VENUE_INVALID', `Venue inválida: ${venue}`);
  }

  const price = intent.price === 'market' ? 'market' : assertPositiveNumberString(intent.price, 'price');
  const reduceOnly = Boolean(intent.reduceOnly);

  if (venue !== 'perp' && reduceOnly) {
    throw new OperatorError('ORDER_REDUCE_ONLY_INVALID', 'reduce-only só é permitido para perp.');
  }

  const normalized = {
    ...base,
    chain: 'hyperliquid',
    side,
    venue,
    market: normalizeMarketSymbol(intent.market),
    amount: assertPositiveNumberString(intent.amount, 'amount'),
    price,
    reduceOnly
  };

  if (intent.leverage != null) {
    normalized.leverage = assertPositiveNumberString(intent.leverage, 'leverage');
  }

  if (intent.slippageBps != null) {
    normalized.slippageBps = assertNonNegativeNumberString(intent.slippageBps, 'slippageBps');
  }

  if (intent.tif != null) {
    const tif = String(intent.tif);
    if (!['Alo', 'Ioc', 'Gtc'].includes(tif)) {
      throw new OperatorError('ORDER_TIF_INVALID', `TIF inválido: ${tif}`);
    }
    normalized.tif = tif;
  }

  if (intent.cloid != null) {
    if (!CLOID.test(intent.cloid)) {
      throw new OperatorError('ORDER_CLOID_INVALID', 'cloid inválido. Esperado 0x + 16 bytes hex.', {
        cloid: intent.cloid
      });
    }
    normalized.cloid = intent.cloid.toLowerCase();
  }

  if (includeOrderRef) {
    const orderRef = intent.orderRef;
    if (!orderRef || !['oid', 'cloid'].includes(orderRef.type)) {
      throw new OperatorError('ORDER_REF_INVALID', 'orderRef inválido para modify/cancel', { orderRef });
    }

    if (orderRef.type === 'oid') {
      if (!/^[0-9]+$/.test(String(orderRef.value))) {
        throw new OperatorError('ORDER_REF_INVALID', 'oid inválido', { orderRef });
      }
      normalized.orderRef = {
        type: 'oid',
        value: String(orderRef.value)
      };
    } else {
      if (!CLOID.test(String(orderRef.value))) {
        throw new OperatorError('ORDER_REF_INVALID', 'cloid inválido', { orderRef });
      }
      normalized.orderRef = {
        type: 'cloid',
        value: String(orderRef.value).toLowerCase()
      };
    }
  }

  return normalized;
}


function normalizeHyperliquidDeposit(intent, base) {
  const asset = normalizeAsset(intent.asset);
  if (asset !== 'USDC') {
    throw new OperatorError('HL_DEPOSIT_ASSET_UNSUPPORTED', 'Hyperliquid deposit suporta apenas USDC', { asset });
  }

  return {
    ...base,
    chain: 'hyperliquid',
    asset,
    amount: assertPositiveNumberString(intent.amount, 'amount'),
    toPerp: intent.toPerp !== false
  };
}

function normalizeHyperliquidCancel(intent, base) {
  const venue = intent.venue;
  if (!['spot', 'perp'].includes(venue)) {
    throw new OperatorError('ORDER_VENUE_INVALID', `Venue inválida: ${venue}`);
  }

  const orderRef = intent.orderRef;
  if (!orderRef || !['oid', 'cloid'].includes(orderRef.type)) {
    throw new OperatorError('ORDER_REF_INVALID', 'orderRef inválido para cancel', { orderRef });
  }

  if (orderRef.type === 'oid' && !/^[0-9]+$/.test(String(orderRef.value))) {
    throw new OperatorError('ORDER_REF_INVALID', 'oid inválido para cancel', { orderRef });
  }

  if (orderRef.type === 'cloid' && !CLOID.test(String(orderRef.value))) {
    throw new OperatorError('ORDER_REF_INVALID', 'cloid inválido para cancel', { orderRef });
  }

  return {
    ...base,
    chain: 'hyperliquid',
    venue,
    market: normalizeMarketSymbol(intent.market),
    orderRef: {
      type: orderRef.type,
      value: orderRef.type === 'cloid' ? String(orderRef.value).toLowerCase() : String(orderRef.value)
    }
  };
}

export function normalizeIntent(intent) {
  const base = {
    raw: intent.raw,
    language: intent.language,
    action: intent.action
  };

  if (intent.action === 'portfolio_balance') {
    return {
      ...base,
      action: 'portfolio_balance',
      chains: ['base', 'solana', 'hyperliquid']
    };
  }

  if (intent.action === 'transfer') {
    const asset = normalizeAsset(intent.asset);
    const to = intent.to;
    const byAddress = inferChainFromAddress(to);

    let chain = normalizeChain(intent.chain);
    if (!chain) {
      if (asset === 'ETH') chain = 'base';
      else if (asset === 'SOL') chain = 'solana';
      else chain = byAddress;
    }

    if (!chain) {
      throw new OperatorError(
        'CHAIN_AMBIGUOUS',
        'Chain ambígua para transferência. Informe explicitamente "on base" ou "na solana".'
      );
    }

    if (chain === 'base' && !EVM_ADDRESS.test(to)) {
      throw new OperatorError('ADDRESS_INVALID', 'Destinatário inválido para Base', { to });
    }

    if (chain === 'solana' && !SOLANA_ADDRESS.test(to)) {
      throw new OperatorError('ADDRESS_INVALID', 'Destinatário inválido para Solana', { to });
    }

    if (chain === 'base' && asset !== 'ETH') {
      throw new OperatorError(
        'TRANSFER_ASSET_UNSUPPORTED',
        'Transferência nativa em Base suporta apenas ETH no MVP.',
        { chain, asset }
      );
    }

    if (chain === 'solana' && asset !== 'SOL') {
      throw new OperatorError(
        'TRANSFER_ASSET_UNSUPPORTED',
        'Transferência nativa em Solana suporta apenas SOL no MVP.',
        { chain, asset }
      );
    }

    return {
      ...base,
      chain,
      amount: assertPositiveNumberString(intent.amount, 'amount'),
      asset,
      to
    };
  }

  if (intent.action === 'hl_order') {
    return normalizeHyperliquidOrderLike(intent, base);
  }

  if (intent.action === 'hl_modify') {
    return normalizeHyperliquidOrderLike(intent, base, { includeOrderRef: true });
  }

  if (intent.action === 'hl_cancel') {
    return normalizeHyperliquidCancel(intent, base);
  }

  if (intent.action === 'hl_deposit') {
    return normalizeHyperliquidDeposit(intent, base);
  }

  if (intent.action === 'swap_jupiter' || intent.action === 'swap_raydium') {
    const assetIn = normalizeAsset(intent.assetIn);
    const assetOut = normalizeAsset(intent.assetOut);

    if (assetIn === assetOut) {
      throw new OperatorError('SWAP_ASSET_INVALID', 'assetIn e assetOut devem ser diferentes');
    }

    return {
      ...base,
      chain: 'solana',
      assetIn,
      assetOut,
      amount: assertPositiveNumberString(intent.amount, 'amount'),
      mode: intent.mode ?? 'ExactIn',
      slippageBps: intent.slippageBps != null ? assertNonNegativeNumberString(intent.slippageBps, 'slippageBps') : null,
      recipient: intent.recipient ?? null
    };
  }

  if (intent.action === 'bridge') {
    const fromChain = normalizeChain(intent.fromChain);
    const toChain = normalizeChain(intent.toChain);

    if (fromChain === toChain) {
      throw new OperatorError('BRIDGE_INVALID_ROUTE', 'Origem e destino da bridge não podem ser iguais');
    }

    const recipient = intent.recipient ?? null;
    if (recipient) {
      const infer = inferChainFromAddress(recipient);
      if (!infer || infer !== toChain) {
        throw new OperatorError('BRIDGE_RECIPIENT_INVALID', 'Recipient incompatível com chain de destino', {
          recipient,
          toChain
        });
      }
    }

    return {
      ...base,
      action: 'bridge',
      fromChain,
      toChain,
      amount: assertPositiveNumberString(intent.amount, 'amount'),
      asset: normalizeAsset(intent.asset),
      recipient
    };
  }

  if (intent.action === 'contract_call') {
    const chain = normalizeChain(intent.chain);
    if (chain !== 'base') {
      throw new OperatorError('CONTRACT_CHAIN_UNSUPPORTED', 'MVP de contract call suporta apenas Base');
    }

    if (!EVM_ADDRESS.test(intent.contract)) {
      throw new OperatorError('CONTRACT_ADDRESS_INVALID', 'Endereço de contrato inválido', {
        contract: intent.contract
      });
    }

    if (!/^0x[a-fA-F0-9]*$/.test(intent.data)) {
      throw new OperatorError('CALLDATA_INVALID', 'Calldata inválida', { data: intent.data });
    }

    return {
      ...base,
      chain,
      contract: intent.contract,
      data: intent.data,
      value: String(Number(intent.value ?? '0'))
    };
  }

  throw new OperatorError('ACTION_UNSUPPORTED', `Ação não suportada: ${intent.action}`);
}
