import crypto from 'node:crypto';
import { parseInstruction } from './parser.mjs';
import { parseExecutionPayload } from './execution-plane.mjs';
import { resolveNativeCommand } from './native-command-router.mjs';
import { normalizeIntent } from './normalize.mjs';
import { loadPolicy, PolicyEngine } from './policy-engine.mjs';
import { buildPlan } from './planner.mjs';
import { logEvent } from './audit-log.mjs';
import {
  cleanupExpired,
  computeIdempotencyKey,
  getRecord,
  markFailure,
  markPending,
  markSuccess
} from './idempotency-store.mjs';
import { assertCanExecute, registerFailure, registerSuccess } from './circuit-breaker.mjs';
import { BaseConnector } from '../connectors/base.mjs';
import { SolanaConnector } from '../connectors/solana.mjs';
import { HyperliquidConnector } from '../connectors/hyperliquid.mjs';
import { DebridgeConnector } from '../connectors/debridge.mjs';
import { JupiterConnector } from '../connectors/jupiter.mjs';
import { RaydiumConnector } from '../connectors/raydium.mjs';
import { PumpfunConnector } from '../connectors/pumpfun.mjs';
import { DefiConnector } from '../connectors/defi.mjs';
import { verifyExecutionPlaneSecurity } from './a2a-security.mjs';
import { registerMentionDelegationDedupe } from './mention-delegation-gate.mjs';
import { ConsolidatedBalanceService } from './portfolio-balance.mjs';
import { OperatorError } from '../utils/errors.mjs';

function randomSuffix() {
  return crypto.randomBytes(4).toString('hex');
}

function normalizeHexKey(value) {
  if (!value) return null;
  const normalized = String(value).trim();
  if (!normalized) return null;
  return normalized.startsWith('0x') ? normalized.toLowerCase() : `0x${normalized.toLowerCase()}`;
}

function hasSolanaKeyConfigured() {
  const b58 = String(process.env.SOLANA_PRIVATE_KEY_B58 ?? '').trim();
  const json = String(process.env.SOLANA_PRIVATE_KEY_JSON ?? '').trim();
  return Boolean(b58 || json);
}

function isReadOnlyAction(action) {
  return action === 'portfolio_balance';
}

function toErrorPayload(error) {
  return {
    code: error.code ?? 'UNHANDLED_ERROR',
    message: error.message,
    details: error.details ?? null
  };
}

function buildFailureResult({
  runId,
  source,
  dryRun,
  idempotencyKey,
  error,
  executionPlane = null,
  canonicalIntent = null
}) {
  return {
    ok: false,
    runId,
    source,
    dryRun,
    idempotencyKey,
    error,
    ...(executionPlane ? { executionPlane } : {}),
    ...(canonicalIntent ? { canonicalIntent } : {})
  };
}

function assertRequiredKeySegregation(policy) {
  if (!policy?.execution?.requireKeySegregation) return;

  const baseKey = normalizeHexKey(process.env.BASE_PRIVATE_KEY);
  const hyperliquidKey = normalizeHexKey(process.env.HYPERLIQUID_API_WALLET_PRIVATE_KEY);
  const solanaReady = hasSolanaKeyConfigured();

  const missing = [];
  if (!baseKey) missing.push('BASE_PRIVATE_KEY');
  if (!solanaReady) missing.push('SOLANA_PRIVATE_KEY_B58|SOLANA_PRIVATE_KEY_JSON');
  if (!hyperliquidKey) missing.push('HYPERLIQUID_API_WALLET_PRIVATE_KEY');

  if (missing.length > 0) {
    throw new OperatorError(
      'KEY_SEGREGATION_KEYS_MISSING',
      'Segregação obrigatória: configure chaves dedicadas para Base, Solana e Hyperliquid.',
      { missing }
    );
  }

  if (baseKey === hyperliquidKey) {
    throw new OperatorError(
      'KEY_SEGREGATION_VIOLATION',
      'BASE_PRIVATE_KEY e HYPERLIQUID_API_WALLET_PRIVATE_KEY não podem ser a mesma chave.'
    );
  }
}

async function hydrateIntentForPolicy(intent, policy) {
  if (!['hl_order', 'hl_modify'].includes(intent.action)) {
    return intent;
  }

  const hyperliquid = new HyperliquidConnector({});
  return hyperliquid.enrichIntentForPolicy(intent, policy);
}

async function prepareExecutionIntent(intent, policy) {
  if (!['hl_order', 'hl_modify'].includes(intent.action)) return intent;

  if (
    intent.price === 'market' &&
    intent.slippageBps == null &&
    policy?.limits?.defaultSlippageBps != null
  ) {
    return {
      ...intent,
      slippageBps: String(Number(policy.limits.defaultSlippageBps))
    };
  }

  return intent;
}

// connectors live implemented via dedicated classes

function shouldFallbackFromJupiter(error) {
  const code = String(error?.code ?? '');
  const message = `${String(error?.message ?? '')} ${String(error?.details?.message ?? '')}`.toLowerCase();

  const looksNetworkIssue =
    message.includes('fetch failed') ||
    message.includes('network') ||
    message.includes('timed out') ||
    message.includes('econnreset') ||
    message.includes('enotfound') ||
    message.includes('eai_again');

  if (code === 'JUPITER_PREFLIGHT_FAILED' || code === 'JUPITER_EXECUTION_FAILED') {
    return looksNetworkIssue;
  }

  if (code === 'JUPITER_HTTP_ERROR') {
    const status = Number(error?.details?.status);
    if (status === 408 || status === 425 || status === 429 || (status >= 500 && status <= 599)) {
      return true;
    }
  }

  return false;
}

async function executeIntent(intent, { dryRun, runId, idempotencyKey }) {
  const base = new BaseConnector({});
  const solana = new SolanaConnector({});
  const hyperliquid = new HyperliquidConnector({});
  const debridge = new DebridgeConnector({ base, solana });
  const jupiter = new JupiterConnector({ solana });
  const raydium = new RaydiumConnector({ solana });
  const pumpfun = new PumpfunConnector({ solana });
  const defi = new DefiConnector({});

  if (intent.action === 'portfolio_balance') {
    const portfolio = new ConsolidatedBalanceService({
      baseConnector: base,
      solanaConnector: solana,
      hyperliquidConnector: hyperliquid
    });

    const snapshot = await portfolio.getSnapshot();

    return {
      connector: 'portfolio',
      snapshot,
      discordMessage: snapshot.discordMessage
    };
  }

  if (intent.action === 'transfer' || intent.action === 'send') {
    if (intent.chain === 'base') {
      return dryRun
        ? { connector: 'base', preflight: await base.preflightNativeTransfer({ to: intent.to, amount: intent.amount }) }
        : { connector: 'base', execution: await base.sendNativeTransfer({ to: intent.to, amount: intent.amount }) };
    }

    if (intent.chain === 'solana') {
      return dryRun
        ? { connector: 'solana', preflight: await solana.preflightNativeTransfer({ to: intent.to, amount: intent.amount }) }
        : { connector: 'solana', execution: await solana.sendNativeTransfer({ to: intent.to, amount: intent.amount }) };
    }

    throw new OperatorError('TRANSFER_CHAIN_UNSUPPORTED', `Chain não suportada para transfer/send: ${intent.chain}`);
  }

  if (intent.action === 'contract_call') {
    return dryRun
      ? {
          connector: 'base',
          preflight: await base.preflightContractCall({
            to: intent.contract,
            data: intent.data,
            value: intent.value
          })
        }
      : {
          connector: 'base',
          execution: await base.sendContractCall({
            to: intent.contract,
            data: intent.data,
            value: intent.value
          })
        };
  }

  if (intent.action === 'hl_order') {
    return dryRun
      ? { connector: 'hyperliquid', preflight: await hyperliquid.preflightOrder(intent) }
      : {
          connector: 'hyperliquid',
          execution: await hyperliquid.placeOrder(intent, {
            runId,
            idempotencyKey
          })
        };
  }

  if (intent.action === 'hl_cancel') {
    return dryRun
      ? { connector: 'hyperliquid', preflight: await hyperliquid.preflightCancel(intent) }
      : {
          connector: 'hyperliquid',
          execution: await hyperliquid.cancelOrder(intent)
        };
  }

  if (intent.action === 'hl_modify') {
    return dryRun
      ? { connector: 'hyperliquid', preflight: await hyperliquid.preflightModify(intent) }
      : {
          connector: 'hyperliquid',
          execution: await hyperliquid.modifyOrder(intent)
        };
  }

  if (intent.action === 'hl_deposit') {
    return dryRun
      ? { connector: 'hyperliquid', preflight: await hyperliquid.preflightDeposit(intent) }
      : {
          connector: 'hyperliquid',
          execution: await hyperliquid.deposit(intent)
        };
  }

  if (intent.action === 'bridge') {
    return dryRun
      ? { connector: 'debridge', preflight: await debridge.preflightBridge(intent) }
      : {
          connector: 'debridge',
          execution: await debridge.executeBridge(intent, {
            runId,
            idempotencyKey
          })
        };
  }

  if (intent.action === 'swap_jupiter') {
    try {
      return dryRun
        ? { connector: 'jupiter', preflight: await jupiter.preflightSwap(intent) }
        : {
            connector: 'jupiter',
            execution: await jupiter.executeSwap(intent, {
              runId,
              idempotencyKey
            })
          };
    } catch (error) {
      if (!shouldFallbackFromJupiter(error)) {
        throw error;
      }

      const fallbackIntent = {
        ...intent,
        action: 'swap_raydium'
      };

      return dryRun
        ? {
            connector: 'raydium',
            fallback: {
              from: 'jupiter',
              reason: error.code ?? 'JUPITER_FAILURE',
              detail: error.message
            },
            preflight: await raydium.preflightSwap(fallbackIntent)
          }
        : {
            connector: 'raydium',
            fallback: {
              from: 'jupiter',
              reason: error.code ?? 'JUPITER_FAILURE',
              detail: error.message
            },
            execution: await raydium.executeSwap(fallbackIntent, {
              runId,
              idempotencyKey
            })
          };
    }
  }

  if (intent.action === 'swap_raydium') {
    return dryRun
      ? { connector: 'raydium', preflight: await raydium.preflightSwap(intent) }
      : {
          connector: 'raydium',
          execution: await raydium.executeSwap(intent, {
            runId,
            idempotencyKey
          })
        };
  }

  if (intent.action === 'swap_pumpfun') {
    return dryRun
      ? { connector: 'pumpfun', preflight: await pumpfun.preflightTrade(intent) }
      : {
          connector: 'pumpfun',
          execution: await pumpfun.executeTrade(intent, {
            runId,
            idempotencyKey
          })
        };
  }

  if (intent.action === 'defi_deposit') {
    return dryRun
      ? { connector: 'defi', preflight: await defi.preflightDeposit(intent) }
      : {
          connector: 'defi',
          execution: await defi.executeDeposit(intent, {
            runId,
            idempotencyKey
          })
        };
  }

  if (intent.action === 'defi_withdraw') {
    return dryRun
      ? { connector: 'defi', preflight: await defi.preflightWithdraw(intent) }
      : {
          connector: 'defi',
          execution: await defi.executeWithdraw(intent, {
            runId,
            idempotencyKey
          })
        };
  }

  throw new OperatorError('ACTION_EXECUTION_UNSUPPORTED', `Ação não suportada para execução: ${intent.action}`);
}

async function runIntentPipeline({
  runId,
  source,
  parsed = null,
  canonicalIntent,
  policyPath = null,
  dryRun = false,
  idempotencyKey = null,
  executionPlane = null,
  prepareIntent = async (intent) => intent
}) {
  let computedIdempotencyKey = idempotencyKey;
  let effectiveDryRun = dryRun;
  let policy;
  let resolvedIntent = canonicalIntent;

  try {
    const policyLoaded = loadPolicy(policyPath);
    policy = policyLoaded.data;

    const policyIntent = await prepareIntent(canonicalIntent, policy);
    resolvedIntent = policyIntent;

    effectiveDryRun = dryRun || policy.execution.defaultDryRun;
    const isReadOnlyIntent = isReadOnlyAction(policyIntent.action);

    const policyEngine = new PolicyEngine(policy);
    const policyResult = policyEngine.evaluate(policyIntent, { isDryRun: effectiveDryRun });

    const plan = buildPlan(policyIntent, { dryRun: effectiveDryRun });

    computedIdempotencyKey = computedIdempotencyKey ?? computeIdempotencyKey(canonicalIntent, policy.version);
    cleanupExpired(policy.idempotency.ttlDays);

    if (executionPlane) {
      logEvent({
        runId,
        event: 'execution_plane.received',
        data: {
          schemaVersion: executionPlane.schemaVersion,
          operation: executionPlane.operation,
          requestId: executionPlane.requestId,
          correlationId: executionPlane.correlationId,
          timestamp: executionPlane.timestamp,
          meta: executionPlane.meta,
          mentionDelegation: executionPlane.mentionDelegation
            ? {
                mode: executionPlane.mentionDelegation.mode,
                channel: executionPlane.mentionDelegation.channel,
                messageId: executionPlane.mentionDelegation.messageId,
                originBotId: executionPlane.mentionDelegation.originBotId,
                targetBotId: executionPlane.mentionDelegation.targetBotId,
                observedAt: executionPlane.mentionDelegation.observedAt,
                ttlSeconds: executionPlane.mentionDelegation.ttlSeconds,
                expiresAt: executionPlane.mentionDelegation.expiresAt,
                dedupeBy: executionPlane.mentionDelegation.dedupeBy
              }
            : null,
          mentionDelegationDedupe: executionPlane.mentionDelegationDedupe ?? null,
          auth: executionPlane.auth
            ? {
                scheme: executionPlane.auth.scheme,
                keyId: executionPlane.auth.keyId,
                nonce: executionPlane.auth.nonce,
                timestamp: executionPlane.auth.timestamp
              }
            : null,
          security: executionPlane.security ?? null
        }
      });
    }

    if (parsed) {
      logEvent({ runId, event: 'intent.parsed', data: parsed });
    }

    logEvent({ runId, event: 'intent.normalized', data: canonicalIntent });
    if (JSON.stringify(policyIntent) !== JSON.stringify(canonicalIntent)) {
      logEvent({ runId, event: 'intent.policy_enriched', data: policyIntent });
    }

    logEvent({ runId, event: 'policy.checked', data: policyResult });
    logEvent({ runId, event: 'plan.generated', data: plan });

    if (!effectiveDryRun && !isReadOnlyIntent) {
      assertRequiredKeySegregation(policy);
      assertCanExecute(policy);

      const existing = getRecord(computedIdempotencyKey);
      if (existing?.status === 'success' || existing?.status === 'pending') {
        throw new OperatorError(
          'IDEMPOTENCY_DUPLICATE',
          `Idempotency key já usada (${existing.status})`,
          existing
        );
      }

      markPending(computedIdempotencyKey, runId);
      logEvent({
        runId,
        event: 'idempotency.pending',
        data: {
          idempotencyKey: computedIdempotencyKey
        }
      });
    }

    const executionResult = await executeIntent(policyIntent, {
      dryRun: effectiveDryRun,
      runId,
      idempotencyKey: computedIdempotencyKey
    });

    if (!effectiveDryRun && !isReadOnlyIntent) {
      markSuccess(computedIdempotencyKey, runId, executionResult);
      logEvent({
        runId,
        event: 'idempotency.success',
        data: {
          idempotencyKey: computedIdempotencyKey
        }
      });
      registerSuccess(policy);
    }

    logEvent({ runId, event: 'execution.completed', data: executionResult });

    return {
      ok: true,
      runId,
      source,
      dryRun: effectiveDryRun,
      idempotencyKey: computedIdempotencyKey,
      ...(executionPlane ? { executionPlane } : {}),
      intent: policyIntent,
      canonicalIntent,
      plan,
      result: executionResult
    };
  } catch (error) {
    const err = toErrorPayload(error);

    logEvent({ runId, event: 'execution.failed', data: err });

    if (!effectiveDryRun && policy && !isReadOnlyAction(resolvedIntent?.action)) {
      if (computedIdempotencyKey) {
        markFailure(computedIdempotencyKey, runId, err);
        logEvent({
          runId,
          event: 'idempotency.failure',
          data: {
            idempotencyKey: computedIdempotencyKey,
            error: err
          }
        });
      }
      registerFailure(policy, err);
    }

    return buildFailureResult({
      runId,
      source,
      dryRun: effectiveDryRun,
      idempotencyKey: computedIdempotencyKey,
      error: err,
      executionPlane,
      canonicalIntent
    });
  }
}

export async function runInstruction({ instruction, policyPath = null, dryRun = false, idempotencyKey = null }) {
  const runId = `run_${Date.now()}_${randomSuffix()}`;

  try {
    const parsed = parseInstruction(instruction);
    const canonicalIntent = normalizeIntent(parsed);

    return runIntentPipeline({
      runId,
      source: 'control_plane',
      parsed,
      canonicalIntent,
      policyPath,
      dryRun,
      idempotencyKey,
      prepareIntent: hydrateIntentForPolicy
    });
  } catch (error) {
    const err = toErrorPayload(error);
    logEvent({ runId, event: 'execution.failed', data: err });

    return buildFailureResult({
      runId,
      source: 'control_plane',
      dryRun,
      idempotencyKey,
      error: err
    });
  }
}

export async function runNativeCommand({ command, policyPath = null, dryRun = false, idempotencyKey = null }) {
  const runId = `run_${Date.now()}_${randomSuffix()}`;
  let nativeCommand = null;

  try {
    try {
      nativeCommand = resolveNativeCommand(command);
    } catch (error) {
      if (error?.code !== 'NATIVE_COMMAND_UNSUPPORTED') {
        throw error;
      }

      // Fallback: accept free-text intents via the same parser/normalizer pipeline.
      // This keeps /saldo aliases working while enabling commands like
      // "troque 1 SOL por USDC" through execute-native callers.
      nativeCommand = {
        command: String(command ?? '').trim(),
        action: 'free_text',
        resolvedInstruction: String(command ?? '').trim(),
        route: 'free_text_fallback'
      };
    }

    const parsed = parseInstruction(nativeCommand.resolvedInstruction);
    const canonicalIntent = normalizeIntent(parsed);

    const pipelineResult = await runIntentPipeline({
      runId,
      source: 'native_command',
      parsed,
      canonicalIntent,
      policyPath,
      dryRun,
      idempotencyKey,
      prepareIntent: hydrateIntentForPolicy
    });

    return {
      ...pipelineResult,
      nativeCommand
    };
  } catch (error) {
    const err = toErrorPayload(error);
    logEvent({ runId, event: 'execution.failed', data: err });

    return {
      ...buildFailureResult({
        runId,
        source: 'native_command',
        dryRun,
        idempotencyKey,
        error: err
      }),
      nativeCommand
    };
  }
}

export async function runExecutionPayload({
  payload,
  policyPath = null,
  dryRun = false,
  idempotencyKey = null
}) {
  const runId = `run_${Date.now()}_${randomSuffix()}`;
  let effectiveDryRunRequest = dryRun;

  try {
    const parsedPayload = parseExecutionPayload(payload);
    effectiveDryRunRequest = dryRun || Boolean(parsedPayload.envelope.dryRun);

    const security = await verifyExecutionPlaneSecurity(payload, {
      dryRun: effectiveDryRunRequest
    });

    const mentionDelegationDedupe = parsedPayload.envelope.mentionDelegation
      ? await registerMentionDelegationDedupe(parsedPayload.envelope.mentionDelegation)
      : null;

    const executionPlane = {
      ...parsedPayload.envelope,
      mentionDelegationDedupe,
      security
    };

    return runIntentPipeline({
      runId,
      source: 'execution_plane',
      canonicalIntent: parsedPayload.canonicalIntent,
      policyPath,
      dryRun: effectiveDryRunRequest,
      idempotencyKey: idempotencyKey ?? parsedPayload.envelope.idempotencyKey,
      executionPlane,
      prepareIntent: prepareExecutionIntent
    });
  } catch (error) {
    const err = toErrorPayload(error);
    logEvent({ runId, event: 'execution.failed', data: err });

    return buildFailureResult({
      runId,
      source: 'execution_plane',
      dryRun: effectiveDryRunRequest,
      idempotencyKey,
      error: err
    });
  }
}
