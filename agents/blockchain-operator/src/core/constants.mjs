export const CHAIN_ALIASES = {
  base: 'base',
  solana: 'solana',
  arbitrum: 'arbitrum',
  arb: 'arbitrum',
  'arbitrum-one': 'arbitrum',
  arbitrumone: 'arbitrum',
  hyperliquid: 'hyperliquid',
  hl: 'hyperliquid',
  hyperevm: 'hyperliquid'
};

export const PRETTY_CHAIN = {
  base: 'Base',
  solana: 'Solana',
  arbitrum: 'Arbitrum',
  hyperliquid: 'Hyperliquid'
};

export const MAINNET_CHAIN_IDS = {
  base: 8453,
  solana: 'mainnet-beta',
  arbitrum: 42161,
  hyperliquid: 'mainnet'
};

export const DEBRIDGE_CHAIN_IDS = {
  base: 8453,
  solana: 7565164,
  arbitrum: 42161
};

export const EVM_SEMANTIC_CHAINS = new Set(['base', 'arbitrum', 'hyperliquid']);

export const STABLE_ASSETS = new Set(['USDC', 'USDT', 'DAI']);
