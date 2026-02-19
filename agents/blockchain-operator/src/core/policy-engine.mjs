import fs from 'node:fs';
import path from 'node:path';
import Ajv2020 from 'ajv/dist/2020.js';
import { fromRoot } from '../utils/paths.mjs';
import { readJson } from '../utils/fs.mjs';
import { OperatorError } from '../utils/errors.mjs';
import { STABLE_ASSETS } from './constants.mjs';

const ajv = new Ajv2020({ allErrors: true, strict: false });
const schema = readJson(fromRoot('config', 'policy.schema.json'));
const validatePolicySchema = ajv.compile(schema);

export function loadPolicy(policyPath = null) {
  const resolved = policyPath ? path.resolve(policyPath) : fromRoot('config', 'policy.safe-default.json');

  if (!fs.existsSync(resolved)) {
    throw new OperatorError('POLICY_NOT_FOUND', `Policy não encontrada: ${resolved}`);
  }

  const policy = readJson(resolved);
  const valid = validatePolicySchema(policy);
  if (!valid) {
    throw new OperatorError('POLICY_INVALID', 'Policy inválida (schema)', {
      errors: validatePolicySchema.errors
    });
  }

  return {
    path: resolved,
    data: policy
  };
}

function inferNotionalUsd(intent) {
  if (intent.action === 'transfer' || intent.action === 'send') {
    if (STABLE_ASSETS.has(intent.asset)) return Number(intent.amount);
    return null;
  }

  if (intent.action === 'bridge') {
    if (STABLE_ASSETS.has(intent.asset)) return Number(intent.amount);
    return null;
  }

  if (intent.action === 'defi_deposit' || intent.action === 'defi_withdraw') {
    if (STABLE_ASSETS.has(intent.asset)) return Number(intent.amount);
    return null;
  }

  if (intent.action === 'swap_jupiter' || intent.action === 'swap_raydium') {
    if (STABLE_ASSETS.has(intent.assetIn)) return Number(intent.amount);
    return null;
  }

  if (intent.action === 'swap_pumpfun') {
    if (intent.amountType === 'quote' && intent.quoteAsset && STABLE_ASSETS.has(intent.quoteAsset)) {
      return Number(intent.amount);
    }

    return null;
  }

  if (['hl_order', 'hl_modify'].includes(intent.action)) {
    if (intent.price !== 'market') {
      return Number(intent.amount) * Number(intent.price);
    }

    if (intent.referencePrice != null) {
      return Number(intent.amount) * Number(intent.referencePrice);
    }

    return null;
  }

  return null;
}

function shouldCheckHyperliquidSymbol(intent) {
  return ['hl_order', 'hl_modify', 'hl_cancel'].includes(intent.action);
}

function isHyperliquidOrderLike(intent) {
  return ['hl_order', 'hl_modify'].includes(intent.action);
}

function requiresRecipientAllowlist(intent) {
  return ['transfer', 'send', 'bridge', 'defi_withdraw'].includes(intent.action);
}

function recipientFromIntent(intent) {
  if (intent.action === 'transfer' || intent.action === 'send') return intent.to;
  if (intent.action === 'bridge') return intent.recipient ?? null;
  if (intent.action === 'defi_deposit' || intent.action === 'defi_withdraw') return intent.recipient ?? null;
  if (intent.action === 'swap_jupiter' || intent.action === 'swap_raydium' || intent.action === 'swap_pumpfun') {
    return intent.recipient ?? null;
  }
  return null;
}

function intentChains(intent) {
  if (intent.action === 'portfolio_balance') {
    if (Array.isArray(intent.chains) && intent.chains.length > 0) {
      return intent.chains.filter(Boolean);
    }

    return ['base', 'solana', 'hyperliquid'];
  }

  if (intent.action === 'bridge') {
    return [intent.fromChain, intent.toChain].filter(Boolean);
  }

  if (intent.chain) {
    return [intent.chain];
  }

  return [];
}

function intentAssets(intent) {
  if (intent.asset) return [intent.asset];

  if (intent.action === 'swap_jupiter' || intent.action === 'swap_raydium') {
    return [intent.assetIn, intent.assetOut].filter(Boolean);
  }

  if (intent.action === 'swap_pumpfun') {
    const assets = [];
    if (intent.baseAsset) assets.push(intent.baseAsset);
    if (intent.quoteAsset) assets.push(intent.quoteAsset);
    if (assets.length === 0 && intent.symbol) assets.push(intent.symbol);
    return assets;
  }

  return [];
}

function notionalCappedActions() {
  return new Set([
    'transfer',
    'send',
    'bridge',
    'defi_deposit',
    'defi_withdraw',
    'swap_jupiter',
    'swap_raydium',
    'swap_pumpfun'
  ]);
}

export class PolicyEngine {
  constructor(policy) {
    this.policy = policy;
  }

  evaluate(intent, { isDryRun }) {
    const checks = [];
    const { execution, allowlists, limits, marketData, routing, reporting } = this.policy;

    if (execution.allowMainnetOnly !== true) {
      throw new OperatorError('POLICY_MAINNET_REQUIRED', 'Policy deve manter allowMainnetOnly=true');
    }
    checks.push({ ok: true, check: 'mainnet_only' });

    if (routing.hyperliquidOperationalRole !== 'destination_l3') {
      throw new OperatorError(
        'POLICY_HL_ROLE_INVALID',
        'Hyperliquid deve permanecer como destination_l3 na policy operacional.'
      );
    }
    checks.push({ ok: true, check: 'hl_destination_l3' });

    if (!['chainlink', 'pyth'].includes(marketData.primaryPriceSource)) {
      throw new OperatorError('POLICY_MARKETDATA_INVALID', 'Primary price source inválido', {
        primaryPriceSource: marketData.primaryPriceSource
      });
    }

    if (!['chainlink', 'pyth'].includes(marketData.fallbackPriceSource)) {
      throw new OperatorError('POLICY_MARKETDATA_INVALID', 'Fallback price source inválido', {
        fallbackPriceSource: marketData.fallbackPriceSource
      });
    }

    checks.push({
      ok: true,
      check: 'market_data_sources',
      primary: marketData.primaryPriceSource,
      fallback: marketData.fallbackPriceSource
    });

    if (!reporting.discordChannelId) {
      throw new OperatorError('POLICY_REPORTING_CHANNEL_REQUIRED', 'discordChannelId obrigatório em reporting');
    }

    checks.push({ ok: true, check: 'reporting_channel_configured', channel: reporting.discordChannelId });

    const chains = intentChains(intent);
    if (chains.length === 0) {
      throw new OperatorError('POLICY_CHAIN_MISSING', 'Intent sem chain definida', {
        action: intent.action
      });
    }

    for (const chain of chains) {
      if (!allowlists.chains.includes(chain)) {
        throw new OperatorError('POLICY_CHAIN_DENIED', `Chain não permitida pela policy: ${chain}`, {
          chain
        });
      }
    }
    checks.push({ ok: true, check: 'allowlist_chains', chains });

    const assets = intentAssets(intent);
    if (allowlists.assets.length > 0) {
      for (const asset of assets) {
        if (!allowlists.assets.includes(asset)) {
          throw new OperatorError('POLICY_ASSET_DENIED', `Asset não permitida: ${asset}`, {
            asset
          });
        }
      }
    }
    checks.push({ ok: true, check: 'allowlist_assets', assets });

    const recipient = recipientFromIntent(intent);
    const recipientRequired = requiresRecipientAllowlist(intent);

    if (execution.requireRecipientAllowlist && (recipientRequired || recipient)) {
      if (allowlists.recipients.length === 0) {
        throw new OperatorError(
          'POLICY_RECIPIENT_ALLOWLIST_REQUIRED',
          'Policy exige recipients allowlist não vazia para operações com recipient.'
        );
      }

      if (recipientRequired && !recipient) {
        if (intent.action === 'bridge') {
          throw new OperatorError(
            'POLICY_BRIDGE_RECIPIENT_REQUIRED',
            'Bridge requer recipient explícito quando withdrawals devem ser whitelisted.'
          );
        }

        throw new OperatorError('POLICY_RECIPIENT_REQUIRED', 'Recipient obrigatório para esta operação.', {
          action: intent.action
        });
      }

      if (recipient && !allowlists.recipients.includes(recipient)) {
        throw new OperatorError('POLICY_RECIPIENT_DENIED', 'Destinatário fora da allowlist', {
          recipient
        });
      }
    }

    if (intent.action === 'contract_call' && allowlists.contracts.length > 0) {
      if (!allowlists.contracts.includes(intent.contract)) {
        throw new OperatorError('POLICY_CONTRACT_DENIED', 'Contrato fora da allowlist', {
          contract: intent.contract
        });
      }
    }

    if (intent.action === 'bridge') {
      if (!routing.bridgeProviders.includes('debridge')) {
        throw new OperatorError(
          'POLICY_BRIDGE_PROVIDER_DENIED',
          'Policy operacional exige deBridge como provider de bridge.'
        );
      }

      if (!routing.bridgeSourceChains.includes(intent.fromChain)) {
        throw new OperatorError('POLICY_BRIDGE_SOURCE_DENIED', 'Chain origem não permitida para bridge', {
          fromChain: intent.fromChain,
          allowedSources: routing.bridgeSourceChains
        });
      }

      const routeAllowed = allowlists.bridgeRoutes.some(
        (r) => r.from === intent.fromChain && r.to === intent.toChain
      );
      if (!routeAllowed) {
        throw new OperatorError('POLICY_BRIDGE_ROUTE_DENIED', 'Rota bridge não permitida', {
          fromChain: intent.fromChain,
          toChain: intent.toChain
        });
      }
    }

    if (shouldCheckHyperliquidSymbol(intent) && allowlists.hyperliquidSymbols.length > 0) {
      if (!allowlists.hyperliquidSymbols.includes(intent.market)) {
        throw new OperatorError('POLICY_HL_SYMBOL_DENIED', 'Símbolo Hyperliquid fora da allowlist', {
          market: intent.market
        });
      }
    }

    if (isHyperliquidOrderLike(intent) && limits.maxOrderSize != null) {
      const amount = Number(intent.amount);
      if (amount > Number(limits.maxOrderSize)) {
        throw new OperatorError('POLICY_ORDER_SIZE_EXCEEDED', 'Tamanho da ordem acima do limite', {
          amount,
          max: limits.maxOrderSize
        });
      }
    }

    if (limits.maxSlippageBps != null && isHyperliquidOrderLike(intent) && intent.price === 'market') {
      if (intent.slippageBps == null) {
        throw new OperatorError(
          'POLICY_SLIPPAGE_REQUIRED',
          'slippageBps obrigatório para market order quando maxSlippageBps estiver definido.'
        );
      }

      if (Number(intent.slippageBps) > Number(limits.maxSlippageBps)) {
        throw new OperatorError('POLICY_SLIPPAGE_EXCEEDED', 'Slippage acima do limite', {
          slippageBps: intent.slippageBps,
          max: limits.maxSlippageBps
        });
      }
    }

    if (limits.maxPerpLeverage != null && isHyperliquidOrderLike(intent) && intent.venue === 'perp') {
      if (intent.leverage != null && Number(intent.leverage) > Number(limits.maxPerpLeverage)) {
        throw new OperatorError('POLICY_LEVERAGE_EXCEEDED', 'Leverage acima do limite', {
          leverage: intent.leverage,
          max: limits.maxPerpLeverage
        });
      }
    }

    const notional = inferNotionalUsd(intent);

    if (limits.maxNotionalUsdPerTx != null && isHyperliquidOrderLike(intent)) {
      if (notional == null) {
        throw new OperatorError(
          'POLICY_NOTIONAL_UNKNOWN',
          'Não foi possível estimar notional USD para aplicar limite. Defina referência de preço ou use ordem limite.'
        );
      }

      if (notional > Number(limits.maxNotionalUsdPerTx)) {
        throw new OperatorError('POLICY_NOTIONAL_EXCEEDED', 'Notional acima do limite por tx', {
          notional,
          max: limits.maxNotionalUsdPerTx
        });
      }
    }

    if (limits.maxNotionalUsdPerTx != null && notionalCappedActions().has(intent.action)) {
      if (notional == null) {
        throw new OperatorError(
          'POLICY_NOTIONAL_UNKNOWN',
          'Não foi possível estimar notional USD para aplicar limite. Defina ativo estável ou referência de preço.'
        );
      }

      if (notional > Number(limits.maxNotionalUsdPerTx)) {
        throw new OperatorError('POLICY_NOTIONAL_EXCEEDED', 'Notional acima do limite por tx', {
          notional,
          max: limits.maxNotionalUsdPerTx
        });
      }
    }

    checks.push({ ok: true, check: 'limits_notional', notional });

    const simulationGuardActions = new Set([
      'hl_order',
      'hl_modify',
      'hl_cancel',
      'bridge',
      'swap_jupiter',
      'swap_raydium',
      'swap_pumpfun',
      'defi_deposit',
      'defi_withdraw'
    ]);

    if (!isDryRun && execution.requireSimulation && simulationGuardActions.has(intent.action)) {
      checks.push({ ok: true, check: 'requires_preflight_for_live' });
    }

    return {
      checks,
      notionalUsd: notional
    };
  }
}
