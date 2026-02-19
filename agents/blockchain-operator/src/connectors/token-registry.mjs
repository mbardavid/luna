import { OperatorError } from '../utils/errors.mjs';

const SOLANA_MINT = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
const EVM_ADDRESS = /^0x[a-fA-F0-9]{40}$/;

export const SOLANA_TOKENS = Object.freeze({
  SOL: {
    symbol: 'SOL',
    mint: 'So11111111111111111111111111111111111111112',
    decimals: 9,
    native: true
  },
  WSOL: {
    symbol: 'WSOL',
    mint: 'So11111111111111111111111111111111111111112',
    decimals: 9,
    native: false
  },
  USDC: {
    symbol: 'USDC',
    mint: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
    decimals: 6,
    native: false
  },
  USDT: {
    symbol: 'USDT',
    mint: 'Es9vMFrzaCERmJfrF4H2g8mWf9VfKfN7L3A5kZZgwyUr',
    decimals: 6,
    native: false
  }
});

export const BASE_TOKENS = Object.freeze({
  ETH: {
    symbol: 'ETH',
    decimals: 18,
    native: true,
    address: null
  },
  WETH: {
    symbol: 'WETH',
    decimals: 18,
    native: false,
    address: '0x4200000000000000000000000000000000000006'
  },
  USDC: {
    symbol: 'USDC',
    decimals: 6,
    native: false,
    address: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913'
  },
  USDT: {
    symbol: 'USDT',
    decimals: 6,
    native: false,
    address: '0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2'
  }
});

const SOLANA_MINT_LOOKUP = new Map(
  Object.values(SOLANA_TOKENS).map((token) => [token.mint, token])
);

function normalizeNumericString(value, field, errorCode) {
  if (value == null) {
    throw new OperatorError(errorCode, `${field} é obrigatório`, { field, value });
  }

  const raw = String(value).trim();
  if (!raw) {
    throw new OperatorError(errorCode, `${field} inválido`, { field, value });
  }

  if (!/^[0-9]+(?:\.[0-9]+)?$/.test(raw)) {
    throw new OperatorError(errorCode, `${field} inválido`, { field, value });
  }

  return raw;
}

export function decimalToAtomic(value, decimals, { field = 'amount', errorCode = 'TOKEN_AMOUNT_INVALID' } = {}) {
  const normalized = normalizeNumericString(value, field, errorCode);
  const [intPartRaw, fracRaw = ''] = normalized.split('.');

  if (fracRaw.length > decimals) {
    throw new OperatorError(errorCode, `${field} possui casas decimais acima do permitido`, {
      field,
      value,
      decimals
    });
  }

  const fracPadded = `${fracRaw}${'0'.repeat(decimals)}`.slice(0, decimals);
  const intPart = intPartRaw.replace(/^0+/, '') || '0';
  const atomic = `${intPart}${fracPadded}`.replace(/^0+/, '') || '0';

  if (atomic === '0') {
    throw new OperatorError(errorCode, `${field} deve ser maior que zero`, { field, value });
  }

  return atomic;
}

export function atomicToDecimal(value, decimals) {
  const raw = String(value).replace(/^0+/, '') || '0';
  if (!/^\d+$/.test(raw)) return null;

  const padded = raw.padStart(decimals + 1, '0');
  const intPart = padded.slice(0, padded.length - decimals);
  const fracPart = padded.slice(padded.length - decimals).replace(/0+$/, '');

  return fracPart ? `${intPart}.${fracPart}` : intPart;
}

export function resolveSolanaToken(assetOrMint, {
  field = 'asset',
  errorCode = 'SOLANA_TOKEN_UNSUPPORTED',
  defaultDecimals = 6
} = {}) {
  if (!assetOrMint) {
    throw new OperatorError(errorCode, `${field} é obrigatório`, { field, value: assetOrMint });
  }

  const raw = String(assetOrMint).trim();
  const symbol = raw.toUpperCase();

  if (SOLANA_TOKENS[symbol]) {
    return SOLANA_TOKENS[symbol];
  }

  if (SOLANA_MINT.test(raw) && SOLANA_MINT_LOOKUP.has(raw)) {
    return SOLANA_MINT_LOOKUP.get(raw);
  }

  if (SOLANA_MINT.test(raw)) {
    return {
      symbol,
      mint: raw,
      decimals: defaultDecimals,
      native: false,
      inferred: true
    };
  }

  throw new OperatorError(errorCode, `Token Solana não suportado: ${raw}`, {
    field,
    value: assetOrMint
  });
}

export function resolveBaseToken(assetOrAddress, { field = 'asset', errorCode = 'BASE_TOKEN_UNSUPPORTED' } = {}) {
  if (!assetOrAddress) {
    throw new OperatorError(errorCode, `${field} é obrigatório`, { field, value: assetOrAddress });
  }

  const raw = String(assetOrAddress).trim();
  const symbol = raw.toUpperCase();

  if (BASE_TOKENS[symbol]) {
    return BASE_TOKENS[symbol];
  }

  if (EVM_ADDRESS.test(raw)) {
    const found = Object.values(BASE_TOKENS).find(
      (token) => token.address && token.address.toLowerCase() === raw.toLowerCase()
    );

    if (found) return found;

    throw new OperatorError(errorCode, `Token Base não mapeado para endereço ${raw}`, {
      field,
      value: assetOrAddress
    });
  }

  throw new OperatorError(errorCode, `Token Base não suportado: ${raw}`, {
    field,
    value: assetOrAddress
  });
}

export function maybeResolvePumpfunMintBySymbol(symbol) {
  const raw = process.env.PUMPFUN_SYMBOL_MAP_JSON;
  if (!raw) return null;

  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return null;

    const key = String(symbol).toUpperCase();
    const value = parsed[key] ?? parsed[String(symbol)] ?? null;
    if (!value) return null;

    const mint = String(value).trim();
    return SOLANA_MINT.test(mint) ? mint : null;
  } catch {
    return null;
  }
}
