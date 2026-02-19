import { OperatorError } from '../utils/errors.mjs';

function parseLocaleNumber(value) {
  return value.replace(',', '.');
}

function detectLanguage(text) {
  const ptHints =
    /\b(enviar|transferir|comprar|vender|cancelar|modificar|alterar|para|na|no|mercado|ponte|contrato|saldo)\b/i;
  return ptHints.test(text) ? 'pt' : 'en';
}

function parsePortfolioBalance(text) {
  const normalized = text.trim();

  if (/^\/?(?:saldo|balance|balances)(?:\s+(?:consolidado|consolidated))?$/i.test(normalized)) {
    return {
      action: 'portfolio_balance'
    };
  }

  if (/^(?:mostrar|show)\s+(?:saldo|balance)(?:\s+(?:consolidado|consolidated))?$/i.test(normalized)) {
    return {
      action: 'portfolio_balance'
    };
  }

  return null;
}

function parseTransfer(text) {
  const regex = /(?:send|transfer|enviar|transferir)\s+([0-9]+(?:[\.,][0-9]+)?)\s+([a-zA-Z0-9_:@/.-]+)\s+(?:to|para)\s+([a-zA-Z0-9]{32,}|0x[a-fA-F0-9]{40})(?:\s+(?:on|na|no|em)\s+([a-zA-Z]+))?/i;
  const match = text.match(regex);
  if (!match) return null;

  return {
    action: 'transfer',
    amount: parseLocaleNumber(match[1]),
    asset: match[2],
    to: match[3],
    chain: match[4] ?? null
  };
}

function inferVenue(venueRaw, market) {
  const normalized = venueRaw?.toLowerCase() ?? null;
  if (normalized === 'spot') return 'spot';
  if (normalized) return 'perp';
  if (market.includes('/')) return 'spot';
  return 'perp';
}

function inferPrice(priceRaw) {
  const normalized = priceRaw?.toLowerCase() ?? null;
  if (!normalized || normalized === 'market' || normalized === 'mercado') return 'market';
  return parseLocaleNumber(normalized);
}

function parseOptionalHyperliquidFields(text) {
  const reduceOnly = /\breduce(?:-|\s)?only\b/i.test(text) || /\bsomente\s+redu(?:ç|c)[aã]o\b/i.test(text);

  const leverageMatch = text.match(/(?:\blev(?:erage)?\b|\balavancagem\b)\s+([0-9]+(?:[\.,][0-9]+)?)/i);
  const leverage = leverageMatch ? parseLocaleNumber(leverageMatch[1]) : null;

  const slippageMatch = text.match(/(?:\bslippage\b|\bdesvio\b)\s+([0-9]+(?:[\.,][0-9]+)?)\s*(?:bps?|bips?)/i);
  const slippageBps = slippageMatch ? parseLocaleNumber(slippageMatch[1]) : null;

  const tifMatch = text.match(/\btif\s+(alo|ioc|gtc)\b/i);
  const tif = tifMatch ? tifMatch[1][0].toUpperCase() + tifMatch[1].slice(1).toLowerCase() : null;

  const cloidMatch = text.match(/\bcloid\s+(0x[a-fA-F0-9]{32})\b/i);
  const cloid = cloidMatch ? cloidMatch[1].toLowerCase() : null;

  return {
    reduceOnly,
    leverage,
    slippageBps,
    tif,
    cloid
  };
}

function parseOrderRef(kindRaw, valueRaw) {
  const kind = kindRaw?.toLowerCase() ?? null;
  const value = String(valueRaw ?? '').trim();

  if (!value) return null;

  const isHexCloid = /^0x[a-fA-F0-9]{32}$/.test(value);
  const isOid = /^[0-9]+$/.test(value);

  if ((kind === 'cloid' || !kind) && isHexCloid) {
    return {
      type: 'cloid',
      value: value.toLowerCase()
    };
  }

  if ((kind === 'oid' || kind === 'id' || !kind) && isOid) {
    return {
      type: 'oid',
      value
    };
  }

  return null;
}

function parseHyperliquidCancel(text) {
  const regex =
    /(?:cancel|cancelar)\s+(?:order|ordem)\s+(?:(cloid|oid|id)\s+)?([0-9]+|0x[a-fA-F0-9]{32})\s+([a-zA-Z0-9_:@/.-]+)(?:\s+(spot|perp|perpetual|perpetuo|perpétuo))?(?:\s+(?:on|na|no|em)\s+(hyperliquid|hl))?/i;
  const match = text.match(regex);
  if (!match) return null;

  const orderRef = parseOrderRef(match[1], match[2]);
  if (!orderRef) return null;

  const market = match[3];
  const venue = inferVenue(match[4], market);

  return {
    action: 'hl_cancel',
    chain: 'hyperliquid',
    market,
    venue,
    orderRef
  };
}

function parseHyperliquidModify(text) {
  const regex =
    /(?:modify|edit|change|alterar|modificar)\s+(?:order|ordem)\s+(?:(cloid|oid|id)\s+)?([0-9]+|0x[a-fA-F0-9]{32})\s+(buy|sell|comprar|vender)\s+([0-9]+(?:[\.,][0-9]+)?)\s+([a-zA-Z0-9_:@/.-]+)(?:\s+(spot|perp|perpetual|perpetuo|perpétuo))?(?:\s+(?:at|@|a)\s+(market|mercado|[0-9]+(?:[\.,][0-9]+)?))?(?:\s+(?:on|na|no|em)\s+(hyperliquid|hl))?/i;
  const match = text.match(regex);
  if (!match) return null;

  const orderRef = parseOrderRef(match[1], match[2]);
  if (!orderRef) return null;

  const sideRaw = match[3].toLowerCase();
  const side = sideRaw === 'buy' || sideRaw === 'comprar' ? 'buy' : 'sell';
  const market = match[5];
  const optional = parseOptionalHyperliquidFields(text);

  return {
    action: 'hl_modify',
    chain: 'hyperliquid',
    orderRef,
    side,
    amount: parseLocaleNumber(match[4]),
    market,
    venue: inferVenue(match[6], market),
    price: inferPrice(match[7]),
    ...optional
  };
}

function parseHyperliquidOrder(text) {
  const regex =
    /(buy|sell|comprar|vender)\s+([0-9]+(?:[\.,][0-9]+)?)\s+([a-zA-Z0-9_:@/.-]+)(?:\s+(spot|perp|perpetual|perpetuo|perpétuo))?(?:\s+(?:at|@|a)\s+(market|mercado|[0-9]+(?:[\.,][0-9]+)?))?(?:\s+(?:on|na|no|em)\s+(hyperliquid|hl))?/i;
  const match = text.match(regex);
  if (!match) return null;

  const sideRaw = match[1].toLowerCase();
  const side = sideRaw === 'buy' || sideRaw === 'comprar' ? 'buy' : 'sell';
  const market = match[3];
  const optional = parseOptionalHyperliquidFields(text);

  return {
    action: 'hl_order',
    chain: 'hyperliquid',
    side,
    amount: parseLocaleNumber(match[2]),
    market,
    venue: inferVenue(match[4], market),
    price: inferPrice(match[5]),
    ...optional
  };
}

function parseSwap(text) {
  const regex =
    /(?:swap|trocar|troque|converta|converter)\s+([0-9]+(?:[\.,][0-9]+)?)\s+([a-zA-Z0-9_:@/.-]+)\s+(?:for|to|por|em)\s+([a-zA-Z0-9_:@/.-]+)(?:\s+(?:on|na|no|em)\s+(jupiter|raydium))?(?:\s+(?:slippage|desvio)\s+([0-9]+(?:[\.,][0-9]+)?)\s*(?:bps?|bips?))?/i;
  const match = text.match(regex);
  if (!match) return null;

  const venue = match[4]?.toLowerCase() ?? 'jupiter';

  return {
    action: venue === 'raydium' ? 'swap_raydium' : 'swap_jupiter',
    chain: 'solana',
    amount: parseLocaleNumber(match[1]),
    assetIn: match[2],
    assetOut: match[3],
    mode: 'ExactIn',
    slippageBps: match[5] ? parseLocaleNumber(match[5]) : null,
    recipient: null
  };
}

function parseBridge(text) {
  const regex =
    /(?:bridge|bridging|bridgear|ponte|mover)\s+(?:de\s+)?([0-9]+(?:[\.,][0-9]+)?)\s+([a-zA-Z0-9_:@/.-]+)\s+(?:from|de)\s+(base|solana)\s+(?:to|para)\s+(base|solana)(?:\s+(?:to|para)\s+([a-zA-Z0-9]{32,}|0x[a-fA-F0-9]{40}))?/i;
  const match = text.match(regex);
  if (!match) return null;

  return {
    action: 'bridge',
    amount: parseLocaleNumber(match[1]),
    asset: match[2],
    fromChain: match[3],
    toChain: match[4],
    recipient: match[5] ?? null
  };
}

function parseContractCall(text) {
  const regex =
    /(?:call|chamar|executar|interagir(?:\s+com)?)\s+(?:contract|contrato)\s+(0x[a-fA-F0-9]{40})\s+(?:on|na|no|em)\s+(base)\s+data\s+(0x[a-fA-F0-9]*)(?:\s+(?:value|valor)\s+([0-9]+(?:[\.,][0-9]+)?))?/i;
  const match = text.match(regex);
  if (!match) return null;

  return {
    action: 'contract_call',
    chain: 'base',
    contract: match[1],
    data: match[3],
    value: match[4] ? parseLocaleNumber(match[4]) : '0'
  };
}

export function parseInstruction(rawInstruction) {
  const text = rawInstruction.trim();

  for (const parser of [
    parsePortfolioBalance,
    parseTransfer,
    parseHyperliquidCancel,
    parseHyperliquidModify,
    parseHyperliquidOrder,
    parseSwap,
    parseBridge,
    parseContractCall
  ]) {
    const parsed = parser(text);
    if (parsed) {
      return {
        language: detectLanguage(text),
        raw: rawInstruction,
        ...parsed
      };
    }
  }

  throw new OperatorError(
    'INTENT_PARSE_ERROR',
    'Não foi possível interpretar a instrução. Use formato explícito (transfer/order/bridge/contract call) ou /saldo.',
    { rawInstruction }
  );
}
