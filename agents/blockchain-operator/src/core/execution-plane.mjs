import Ajv2020 from 'ajv/dist/2020.js';
import { OperatorError } from '../utils/errors.mjs';

const EVM_ADDRESS = /^0x[a-fA-F0-9]{40}$/;
const SOLANA_ADDRESS = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
const CLOID = /^0x[a-fA-F0-9]{32}$/;
const MACHINE_ID = /^[a-zA-Z0-9._:-]{6,128}$/;
const A2A_NONCE = /^[A-Za-z0-9._:-]{8,128}$/;
const BOT_ID = /^[a-zA-Z0-9._:-]{2,128}$/;
const DISCORD_CHANNEL_REF = /^discord:(channel|thread):[0-9]{6,30}$/;
const DISCORD_MESSAGE_ID = /^[0-9]{6,30}$/;
const RISK_CLASSIFICATIONS = new Set(['read', 'diagnostic', 'sensitive', 'live']);

export const EXECUTION_PLANE_VERSION = 'v1';

export const EXECUTION_OPERATIONS = Object.freeze({
  BRIDGE: 'bridge',
  SWAP_JUPITER: 'swap.jupiter',
  SWAP_RAYDIUM: 'swap.raydium',
  SWAP_PUMPFUN: 'swap.pumpfun',
  DEFI_DEPOSIT: 'defi.deposit',
  DEFI_WITHDRAW: 'defi.withdraw',
  HL_SPOT_ORDER: 'hyperliquid.spot.order',
  HL_PERP_ORDER: 'hyperliquid.perp.order',
  TRANSFER: 'transfer',
  SEND: 'send',
  HL_CANCEL: 'hyperliquid.cancel',
  HL_MODIFY: 'hyperliquid.modify',
  HL_DEPOSIT: 'hyperliquid.deposit'
});

const OPERATION_VALUES = Object.values(EXECUTION_OPERATIONS);

const numericSchema = {
  anyOf: [{ type: 'number' }, { type: 'string', minLength: 1, maxLength: 64 }]
};

const orderRefSchema = {
  type: 'object',
  required: ['type', 'value'],
  properties: {
    type: { type: 'string', enum: ['oid', 'cloid'] },
    value: {
      anyOf: [
        { type: 'integer', minimum: 0 },
        { type: 'string', minLength: 1, maxLength: 128 }
      ]
    }
  },
  additionalProperties: false
};

const envelopeSchema = {
  type: 'object',
  required: ['schemaVersion', 'plane', 'operation', 'requestId', 'correlationId', 'intent'],
  properties: {
    schemaVersion: { type: 'string', const: EXECUTION_PLANE_VERSION },
    plane: { type: 'string', const: 'execution' },
    operation: { type: 'string', enum: OPERATION_VALUES },
    requestId: { type: 'string', pattern: MACHINE_ID.source },
    correlationId: { type: 'string', pattern: MACHINE_ID.source },
    idempotencyKey: { type: 'string', minLength: 8, maxLength: 256 },
    timestamp: {
      type: 'string',
      pattern: '^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d{1,3})?Z$'
    },
    dryRun: { type: 'boolean' },
    intent: { type: 'object' },
    meta: { type: 'object' },
    auth: {
      type: 'object',
      required: ['scheme', 'keyId', 'nonce', 'timestamp', 'signature'],
      properties: {
        scheme: { type: 'string', const: 'hmac-sha256-v1' },
        keyId: { type: 'string', pattern: MACHINE_ID.source },
        nonce: { type: 'string', pattern: A2A_NONCE.source },
        timestamp: {
          type: 'string',
          pattern: '^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d{1,3})?Z$'
        },
        signature: { type: 'string', minLength: 16, maxLength: 512 }
      },
      additionalProperties: false
    }
  },
  additionalProperties: false
};

const operationSchemas = {
  [EXECUTION_OPERATIONS.BRIDGE]: {
    type: 'object',
    required: ['fromChain', 'toChain', 'asset', 'amount'],
    properties: {
      fromChain: { type: 'string', enum: ['base', 'solana'] },
      toChain: { type: 'string', enum: ['base', 'solana'] },
      asset: { type: 'string', minLength: 2, maxLength: 32 },
      amount: numericSchema,
      recipient: { type: 'string', minLength: 32, maxLength: 64 },
      provider: { type: 'string', enum: ['debridge'] },
      quoteId: { type: 'string', minLength: 1, maxLength: 128 },
      maxSlippageBps: { type: 'integer', minimum: 0, maximum: 10000 }
    },
    additionalProperties: false
  },
  [EXECUTION_OPERATIONS.SWAP_JUPITER]: {
    type: 'object',
    required: ['chain', 'inAsset', 'outAsset', 'amount'],
    properties: {
      chain: { type: 'string', const: 'solana' },
      inAsset: { type: 'string', minLength: 2, maxLength: 32 },
      outAsset: { type: 'string', minLength: 2, maxLength: 32 },
      amount: numericSchema,
      mode: { type: 'string', enum: ['ExactIn', 'ExactOut'] },
      slippageBps: { type: 'integer', minimum: 0, maximum: 10000 },
      recipient: { type: 'string', minLength: 32, maxLength: 64 },
      routeHint: { type: 'string', minLength: 1, maxLength: 128 }
    },
    additionalProperties: false
  },
  [EXECUTION_OPERATIONS.SWAP_RAYDIUM]: {
    type: 'object',
    required: ['chain', 'inAsset', 'outAsset', 'amount'],
    properties: {
      chain: { type: 'string', const: 'solana' },
      inAsset: { type: 'string', minLength: 2, maxLength: 32 },
      outAsset: { type: 'string', minLength: 2, maxLength: 32 },
      amount: numericSchema,
      slippageBps: { type: 'integer', minimum: 0, maximum: 10000 },
      poolId: { type: 'string', minLength: 1, maxLength: 160 },
      recipient: { type: 'string', minLength: 32, maxLength: 64 }
    },
    additionalProperties: false
  },
  [EXECUTION_OPERATIONS.SWAP_PUMPFUN]: {
    type: 'object',
    required: ['chain', 'side', 'symbol', 'amount'],
    properties: {
      chain: { type: 'string', const: 'solana' },
      side: { type: 'string', enum: ['buy', 'sell'] },
      symbol: { type: 'string', minLength: 1, maxLength: 24 },
      mint: { type: 'string', minLength: 32, maxLength: 64 },
      amount: numericSchema,
      amountType: { type: 'string', enum: ['base', 'quote'] },
      slippageBps: { type: 'integer', minimum: 0, maximum: 10000 },
      recipient: { type: 'string', minLength: 32, maxLength: 64 }
    },
    additionalProperties: false
  },
  [EXECUTION_OPERATIONS.DEFI_DEPOSIT]: {
    type: 'object',
    required: ['chain', 'protocol', 'target', 'asset', 'amount'],
    properties: {
      chain: { type: 'string', enum: ['base', 'solana'] },
      protocol: { type: 'string', minLength: 2, maxLength: 64 },
      target: { type: 'string', minLength: 2, maxLength: 128 },
      asset: { type: 'string', minLength: 2, maxLength: 32 },
      amount: numericSchema,
      minSharesOut: numericSchema,
      recipient: { type: 'string', minLength: 32, maxLength: 64 }
    },
    additionalProperties: false
  },
  [EXECUTION_OPERATIONS.DEFI_WITHDRAW]: {
    type: 'object',
    required: ['chain', 'protocol', 'target', 'asset', 'amount', 'recipient'],
    properties: {
      chain: { type: 'string', enum: ['base', 'solana'] },
      protocol: { type: 'string', minLength: 2, maxLength: 64 },
      target: { type: 'string', minLength: 2, maxLength: 128 },
      asset: { type: 'string', minLength: 2, maxLength: 32 },
      amount: numericSchema,
      recipient: { type: 'string', minLength: 32, maxLength: 64 },
      amountType: { type: 'string', enum: ['asset', 'shares'] }
    },
    additionalProperties: false
  },
  [EXECUTION_OPERATIONS.HL_SPOT_ORDER]: {
    type: 'object',
    required: ['market', 'side', 'amount', 'price'],
    properties: {
      market: { type: 'string', minLength: 3, maxLength: 32 },
      side: { type: 'string', enum: ['buy', 'sell'] },
      amount: numericSchema,
      price: {
        anyOf: [{ type: 'string', const: 'market' }, numericSchema]
      },
      slippageBps: { type: 'integer', minimum: 0, maximum: 10000 },
      tif: { type: 'string', enum: ['Alo', 'Ioc', 'Gtc'] },
      cloid: { type: 'string', pattern: CLOID.source }
    },
    additionalProperties: false
  },
  [EXECUTION_OPERATIONS.HL_PERP_ORDER]: {
    type: 'object',
    required: ['market', 'side', 'amount', 'price'],
    properties: {
      market: { type: 'string', minLength: 1, maxLength: 32 },
      side: { type: 'string', enum: ['buy', 'sell'] },
      amount: numericSchema,
      price: {
        anyOf: [{ type: 'string', const: 'market' }, numericSchema]
      },
      reduceOnly: { type: 'boolean' },
      leverage: numericSchema,
      slippageBps: { type: 'integer', minimum: 0, maximum: 10000 },
      tif: { type: 'string', enum: ['Alo', 'Ioc', 'Gtc'] },
      cloid: { type: 'string', pattern: CLOID.source },
      referencePrice: numericSchema
    },
    additionalProperties: false
  },
  [EXECUTION_OPERATIONS.TRANSFER]: {
    type: 'object',
    required: ['chain', 'asset', 'amount', 'to'],
    properties: {
      chain: { type: 'string', enum: ['base', 'solana'] },
      asset: { type: 'string', minLength: 2, maxLength: 32 },
      amount: numericSchema,
      to: { type: 'string', minLength: 32, maxLength: 64 },
      memo: { type: 'string', maxLength: 256 }
    },
    allOf: [
      {
        if: {
          properties: { chain: { const: 'base' } },
          required: ['chain']
        },
        then: {
          properties: { asset: { const: 'ETH' } },
          required: ['asset']
        }
      },
      {
        if: {
          properties: { chain: { const: 'solana' } },
          required: ['chain']
        },
        then: {
          properties: { asset: { const: 'SOL' } },
          required: ['asset']
        }
      }
    ],
    additionalProperties: false
  },
  [EXECUTION_OPERATIONS.SEND]: {
    type: 'object',
    required: ['chain', 'asset', 'amount', 'to'],
    properties: {
      chain: { type: 'string', enum: ['base', 'solana'] },
      asset: { type: 'string', minLength: 2, maxLength: 32 },
      amount: numericSchema,
      to: { type: 'string', minLength: 32, maxLength: 64 },
      purpose: { type: 'string', maxLength: 256 },
      memo: { type: 'string', maxLength: 256 }
    },
    allOf: [
      {
        if: {
          properties: { chain: { const: 'base' } },
          required: ['chain']
        },
        then: {
          properties: { asset: { const: 'ETH' } },
          required: ['asset']
        }
      },
      {
        if: {
          properties: { chain: { const: 'solana' } },
          required: ['chain']
        },
        then: {
          properties: { asset: { const: 'SOL' } },
          required: ['asset']
        }
      }
    ],
    additionalProperties: false
  },
  [EXECUTION_OPERATIONS.HL_CANCEL]: {
    type: 'object',
    required: ['venue', 'market', 'orderRef'],
    properties: {
      venue: { type: 'string', enum: ['spot', 'perp'] },
      market: { type: 'string', minLength: 1, maxLength: 32 },
      orderRef: orderRefSchema
    },
    additionalProperties: false
  },

  [EXECUTION_OPERATIONS.HL_DEPOSIT]: {
    type: 'object',
    required: ['asset', 'amount'],
    properties: {
      asset: { type: 'string', const: 'USDC' },
      amount: numericSchema,
      toPerp: { type: 'boolean' }
    },
    additionalProperties: false
  },
  [EXECUTION_OPERATIONS.HL_MODIFY]: {
    type: 'object',
    required: ['venue', 'market', 'orderRef', 'side', 'amount', 'price'],
    properties: {
      venue: { type: 'string', enum: ['spot', 'perp'] },
      market: { type: 'string', minLength: 1, maxLength: 32 },
      orderRef: orderRefSchema,
      side: { type: 'string', enum: ['buy', 'sell'] },
      amount: numericSchema,
      price: {
        anyOf: [{ type: 'string', const: 'market' }, numericSchema]
      },
      reduceOnly: { type: 'boolean' },
      leverage: numericSchema,
      slippageBps: { type: 'integer', minimum: 0, maximum: 10000 },
      tif: { type: 'string', enum: ['Alo', 'Ioc', 'Gtc'] },
      cloid: { type: 'string', pattern: CLOID.source },
      referencePrice: numericSchema
    },
    additionalProperties: false
  }
};

const ajv = new Ajv2020({ allErrors: true, strict: false });
const validateEnvelope = ajv.compile(envelopeSchema);
const validateByOperation = Object.fromEntries(
  Object.entries(operationSchemas).map(([operation, schema]) => [operation, ajv.compile(schema)])
);

function formatAjvErrors(errors = []) {
  return errors.map((error) => ({
    instancePath: error.instancePath || '/',
    schemaPath: error.schemaPath,
    keyword: error.keyword,
    message: error.message
  }));
}

function normalizeAmount(value, field) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new OperatorError('EXECUTION_SCHEMA_AMOUNT_INVALID', `${field} deve ser número positivo`, {
      field,
      value
    });
  }

  return String(parsed);
}

function normalizePositiveOptional(value, field) {
  if (value == null) return undefined;
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new OperatorError('EXECUTION_SCHEMA_NUMBER_INVALID', `${field} deve ser número positivo`, {
      field,
      value
    });
  }

  return String(parsed);
}

function normalizePrice(value, field = 'price') {
  if (value === 'market') return 'market';
  return normalizeAmount(value, field);
}

function normalizeAsset(value, field = 'asset') {
  if (typeof value !== 'string' || !value.trim()) {
    throw new OperatorError('EXECUTION_SCHEMA_ASSET_INVALID', `${field} inválido`, {
      field,
      value
    });
  }

  return value.trim().toUpperCase();
}

function ensureMachineId(value, field) {
  if (!MACHINE_ID.test(String(value ?? ''))) {
    throw new OperatorError('EXECUTION_SCHEMA_ID_INVALID', `${field} inválido`, {
      field,
      value,
      expectedPattern: MACHINE_ID.source
    });
  }

  return String(value);
}

function assertAddressForChain(address, chain, field = 'address') {
  if (chain === 'base') {
    if (!EVM_ADDRESS.test(address)) {
      throw new OperatorError('EXECUTION_SCHEMA_ADDRESS_INVALID', `${field} inválido para Base`, {
        field,
        chain,
        address
      });
    }
    return;
  }

  if (chain === 'solana') {
    if (!SOLANA_ADDRESS.test(address)) {
      throw new OperatorError('EXECUTION_SCHEMA_ADDRESS_INVALID', `${field} inválido para Solana`, {
        field,
        chain,
        address
      });
    }
    return;
  }
}

function normalizeOrderRef(orderRef) {
  if (orderRef.type === 'oid') {
    if (!/^[0-9]+$/.test(String(orderRef.value))) {
      throw new OperatorError('EXECUTION_SCHEMA_ORDER_REF_INVALID', 'oid inválido', {
        orderRef
      });
    }

    return {
      type: 'oid',
      value: String(orderRef.value)
    };
  }

  const value = String(orderRef.value).toLowerCase();
  if (!CLOID.test(value)) {
    throw new OperatorError('EXECUTION_SCHEMA_ORDER_REF_INVALID', 'cloid inválido', {
      orderRef
    });
  }

  return {
    type: 'cloid',
    value
  };
}

function stripUndefined(value) {
  if (Array.isArray(value)) {
    return value.map((item) => stripUndefined(item));
  }

  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([, v]) => v !== undefined)
        .map(([k, v]) => [k, stripUndefined(v)])
    );
  }

  return value;
}

function parseIsoDateTime(value, field) {
  const parsed = Date.parse(String(value ?? ''));
  if (!Number.isFinite(parsed)) {
    throw new OperatorError('EXECUTION_MENTION_DELEGATION_INVALID', `${field} inválido`, {
      field,
      value
    });
  }

  return parsed;
}

function ensureBotId(value, field) {
  const normalized = String(value ?? '').trim();
  if (!BOT_ID.test(normalized)) {
    throw new OperatorError('EXECUTION_MENTION_DELEGATION_INVALID', `${field} inválido`, {
      field,
      value,
      expectedPattern: BOT_ID.source
    });
  }

  return normalized;
}

function normalizeMentionDelegationMeta(meta) {
  const metadata = meta ?? {};

  const hasMentionDelegation =
    metadata.mentionDelegation &&
    typeof metadata.mentionDelegation === 'object' &&
    !Array.isArray(metadata.mentionDelegation);

  if (metadata.mentionDelegationMode == null) {
    if (hasMentionDelegation) {
      throw new OperatorError(
        'EXECUTION_MENTION_DELEGATION_INVALID',
        'meta.mentionDelegation requer mentionDelegationMode=gated.',
        {
          mentionDelegationMode: metadata.mentionDelegationMode
        }
      );
    }

    return null;
  }

  const mode = String(metadata.mentionDelegationMode).trim().toLowerCase();
  if (!mode || mode === 'disabled') {
    if (hasMentionDelegation) {
      throw new OperatorError(
        'EXECUTION_MENTION_DELEGATION_INVALID',
        'meta.mentionDelegation só é permitido quando mentionDelegationMode=gated.',
        {
          mentionDelegationMode: mode || 'disabled'
        }
      );
    }

    return null;
  }

  if (mode !== 'gated') {
    throw new OperatorError(
      'EXECUTION_MENTION_DELEGATION_MODE_UNSUPPORTED',
      'mentionDelegationMode não suportado no execution plane.',
      {
        mode,
        allowed: ['disabled', 'gated']
      }
    );
  }

  if (!hasMentionDelegation) {
    throw new OperatorError(
      'EXECUTION_MENTION_DELEGATION_INVALID',
      'mentionDelegationMode=gated exige meta.mentionDelegation completo.',
      {
        mentionDelegation: metadata.mentionDelegation ?? null
      }
    );
  }

  const mention = metadata.mentionDelegation;

  const channel = String(mention.channel ?? '').trim();
  if (!DISCORD_CHANNEL_REF.test(channel)) {
    throw new OperatorError(
      'EXECUTION_MENTION_DELEGATION_INVALID',
      'channel inválido para mention delegation gated.',
      {
        channel,
        expectedPattern: DISCORD_CHANNEL_REF.source
      }
    );
  }

  const messageId = String(mention.messageId ?? '').trim();
  if (!DISCORD_MESSAGE_ID.test(messageId)) {
    throw new OperatorError(
      'EXECUTION_MENTION_DELEGATION_INVALID',
      'messageId inválido para mention delegation gated.',
      {
        messageId,
        expectedPattern: DISCORD_MESSAGE_ID.source
      }
    );
  }

  const originBotId = ensureBotId(mention.originBotId, 'meta.mentionDelegation.originBotId');
  const targetBotId = ensureBotId(mention.targetBotId, 'meta.mentionDelegation.targetBotId');

  if (originBotId === targetBotId) {
    throw new OperatorError(
      'EXECUTION_MENTION_DELEGATION_LOOP',
      'Anti-loop falhou: originBotId não pode ser igual a targetBotId.',
      {
        originBotId,
        targetBotId
      }
    );
  }

  const dedupeBy = String(mention.dedupeBy ?? 'messageId').trim();
  if (dedupeBy !== 'messageId') {
    throw new OperatorError(
      'EXECUTION_MENTION_DELEGATION_INVALID',
      'dedupeBy inválido para mention delegation gated.',
      {
        dedupeBy,
        expected: 'messageId'
      }
    );
  }

  const ttlSeconds = Number(mention.ttlSeconds);
  if (!Number.isInteger(ttlSeconds) || ttlSeconds < 5 || ttlSeconds > 3600) {
    throw new OperatorError(
      'EXECUTION_MENTION_DELEGATION_INVALID',
      'ttlSeconds inválido para mention delegation gated.',
      {
        ttlSeconds,
        expectedRange: '5..3600'
      }
    );
  }

  const observedAtMs = parseIsoDateTime(mention.observedAt, 'meta.mentionDelegation.observedAt');
  const expiresAtMs = observedAtMs + ttlSeconds * 1000;

  if (expiresAtMs <= Date.now()) {
    throw new OperatorError('EXECUTION_MENTION_DELEGATION_EXPIRED', 'TTL da mention delegation expirou.', {
      messageId,
      observedAt: mention.observedAt,
      ttlSeconds
    });
  }

  const delegatedHumanProxy = mention.delegatedHumanProxy;
  if (!delegatedHumanProxy || typeof delegatedHumanProxy !== 'object' || Array.isArray(delegatedHumanProxy)) {
    throw new OperatorError(
      'EXECUTION_MENTION_DELEGATION_INVALID',
      'delegatedHumanProxy é obrigatório em mention delegation gated.',
      {
        delegatedHumanProxy: delegatedHumanProxy ?? null
      }
    );
  }

  const delegatedMode = String(delegatedHumanProxy.mode ?? '').trim();
  if (delegatedMode !== 'delegated-human-proxy') {
    throw new OperatorError(
      'EXECUTION_MENTION_DELEGATION_INVALID',
      'delegatedHumanProxy.mode deve ser delegated-human-proxy.',
      {
        mode: delegatedMode
      }
    );
  }

  for (const gate of ['policyValidated', 'envelopeValidated', 'riskGatePassed']) {
    if (delegatedHumanProxy[gate] !== true) {
      throw new OperatorError(
        'EXECUTION_MENTION_DELEGATION_INVALID',
        `Gate obrigatório ausente em delegatedHumanProxy: ${gate}`,
        {
          gate,
          value: delegatedHumanProxy[gate] ?? null
        }
      );
    }
  }

  const riskClassificationRaw = delegatedHumanProxy.riskClassification;
  const riskClassification = riskClassificationRaw == null ? null : String(riskClassificationRaw).trim().toLowerCase();

  if (riskClassification && !RISK_CLASSIFICATIONS.has(riskClassification)) {
    throw new OperatorError(
      'EXECUTION_MENTION_DELEGATION_INVALID',
      'riskClassification inválido em delegatedHumanProxy.',
      {
        riskClassification,
        allowed: Array.from(RISK_CLASSIFICATIONS)
      }
    );
  }

  const authorizationRef = delegatedHumanProxy.authorizationRef ?? null;
  if ((riskClassification === 'sensitive' || riskClassification === 'live') && typeof authorizationRef !== 'string') {
    throw new OperatorError(
      'EXECUTION_MENTION_DELEGATION_INVALID',
      'authorizationRef é obrigatório para riskClassification sensitive/live.',
      {
        riskClassification,
        authorizationRef
      }
    );
  }

  return {
    mode: 'gated',
    channel,
    messageId,
    originBotId,
    targetBotId,
    observedAt: new Date(observedAtMs).toISOString(),
    ttlSeconds,
    expiresAt: new Date(expiresAtMs).toISOString(),
    dedupeBy,
    delegatedHumanProxy: {
      mode: 'delegated-human-proxy',
      policyValidated: true,
      envelopeValidated: true,
      riskGatePassed: true,
      riskClassification,
      authorizationRef
    }
  };
}

function normalizeIntent(operation, intent) {
  const base = {
    raw: null,
    language: 'machine'
  };

  if (operation === EXECUTION_OPERATIONS.TRANSFER || operation === EXECUTION_OPERATIONS.SEND) {
    const chain = intent.chain;
    assertAddressForChain(intent.to, chain, 'to');

    const asset = normalizeAsset(intent.asset);
    if (chain === 'base' && asset !== 'ETH') {
      throw new OperatorError(
        'EXECUTION_SCHEMA_TRANSFER_ASSET_UNSUPPORTED',
        'Transfer/send em Base suporta apenas ETH no runtime atual.',
        { chain, asset }
      );
    }

    if (chain === 'solana' && asset !== 'SOL') {
      throw new OperatorError(
        'EXECUTION_SCHEMA_TRANSFER_ASSET_UNSUPPORTED',
        'Transfer/send em Solana suporta apenas SOL no runtime atual.',
        { chain, asset }
      );
    }

    return {
      ...base,
      action: operation === EXECUTION_OPERATIONS.SEND ? 'send' : 'transfer',
      chain,
      asset,
      amount: normalizeAmount(intent.amount, 'amount'),
      to: intent.to,
      memo: intent.memo ?? null,
      purpose: intent.purpose ?? null
    };
  }

  if (operation === EXECUTION_OPERATIONS.BRIDGE) {
    if (intent.fromChain === intent.toChain) {
      throw new OperatorError(
        'EXECUTION_SCHEMA_ROUTE_INVALID',
        'fromChain e toChain não podem ser iguais para bridge',
        { fromChain: intent.fromChain, toChain: intent.toChain }
      );
    }

    if (intent.recipient) {
      assertAddressForChain(intent.recipient, intent.toChain, 'recipient');
    }

    return {
      ...base,
      action: 'bridge',
      fromChain: intent.fromChain,
      toChain: intent.toChain,
      asset: normalizeAsset(intent.asset),
      amount: normalizeAmount(intent.amount, 'amount'),
      recipient: intent.recipient ?? null,
      provider: intent.provider ?? 'debridge',
      quoteId: intent.quoteId ?? null,
      maxSlippageBps: intent.maxSlippageBps ?? null
    };
  }

  if (operation === EXECUTION_OPERATIONS.HL_SPOT_ORDER || operation === EXECUTION_OPERATIONS.HL_PERP_ORDER) {
    const venue = operation === EXECUTION_OPERATIONS.HL_SPOT_ORDER ? 'spot' : 'perp';

    if (venue === 'spot' && !intent.market.includes('/')) {
      throw new OperatorError('EXECUTION_SCHEMA_MARKET_INVALID', 'market spot deve estar no formato BASE/QUOTE', {
        market: intent.market
      });
    }

    return {
      ...base,
      action: 'hl_order',
      chain: 'hyperliquid',
      venue,
      market: String(intent.market).toUpperCase(),
      side: intent.side,
      amount: normalizeAmount(intent.amount, 'amount'),
      price: normalizePrice(intent.price),
      reduceOnly: venue === 'perp' ? Boolean(intent.reduceOnly) : false,
      leverage: venue === 'perp' ? normalizePositiveOptional(intent.leverage, 'leverage') : undefined,
      slippageBps: intent.slippageBps != null ? String(intent.slippageBps) : undefined,
      tif: intent.tif,
      cloid: intent.cloid ? String(intent.cloid).toLowerCase() : undefined,
      referencePrice: normalizePositiveOptional(intent.referencePrice, 'referencePrice')
    };
  }


  if (operation === EXECUTION_OPERATIONS.HL_DEPOSIT) {
    const asset = normalizeAsset(intent.asset);
    if (asset !== 'USDC') {
      throw new OperatorError('EXECUTION_SCHEMA_ASSET_UNSUPPORTED', 'Hyperliquid deposit suporta apenas USDC', {
        asset
      });
    }

    return {
      ...base,
      action: 'hl_deposit',
      chain: 'hyperliquid',
      asset,
      amount: normalizeAmount(intent.amount, 'amount'),
      toPerp: intent.toPerp !== false
    };
  }

  if (operation === EXECUTION_OPERATIONS.HL_CANCEL) {
    return {
      ...base,
      action: 'hl_cancel',
      chain: 'hyperliquid',
      venue: intent.venue,
      market: String(intent.market).toUpperCase(),
      orderRef: normalizeOrderRef(intent.orderRef)
    };
  }

  if (operation === EXECUTION_OPERATIONS.HL_MODIFY) {
    if (intent.venue === 'spot' && intent.reduceOnly) {
      throw new OperatorError('EXECUTION_SCHEMA_REDUCE_ONLY_INVALID', 'reduceOnly não é permitido para spot');
    }

    return {
      ...base,
      action: 'hl_modify',
      chain: 'hyperliquid',
      venue: intent.venue,
      market: String(intent.market).toUpperCase(),
      orderRef: normalizeOrderRef(intent.orderRef),
      side: intent.side,
      amount: normalizeAmount(intent.amount, 'amount'),
      price: normalizePrice(intent.price),
      reduceOnly: intent.venue === 'perp' ? Boolean(intent.reduceOnly) : false,
      leverage: intent.venue === 'perp' ? normalizePositiveOptional(intent.leverage, 'leverage') : undefined,
      slippageBps: intent.slippageBps != null ? String(intent.slippageBps) : undefined,
      tif: intent.tif,
      cloid: intent.cloid ? String(intent.cloid).toLowerCase() : undefined,
      referencePrice: normalizePositiveOptional(intent.referencePrice, 'referencePrice')
    };
  }

  if (operation === EXECUTION_OPERATIONS.SWAP_JUPITER || operation === EXECUTION_OPERATIONS.SWAP_RAYDIUM) {
    if (intent.inAsset.toUpperCase() === intent.outAsset.toUpperCase()) {
      throw new OperatorError('EXECUTION_SCHEMA_SWAP_INVALID', 'inAsset e outAsset devem ser diferentes', {
        inAsset: intent.inAsset,
        outAsset: intent.outAsset
      });
    }

    if (intent.recipient) {
      assertAddressForChain(intent.recipient, 'solana', 'recipient');
    }

    return {
      ...base,
      action: operation === EXECUTION_OPERATIONS.SWAP_JUPITER ? 'swap_jupiter' : 'swap_raydium',
      chain: 'solana',
      assetIn: normalizeAsset(intent.inAsset, 'inAsset'),
      assetOut: normalizeAsset(intent.outAsset, 'outAsset'),
      amount: normalizeAmount(intent.amount, 'amount'),
      slippageBps: intent.slippageBps != null ? String(intent.slippageBps) : undefined,
      recipient: intent.recipient ?? null,
      mode: intent.mode ?? 'ExactIn',
      routeHint: intent.routeHint ?? intent.poolId ?? null
    };
  }

  if (operation === EXECUTION_OPERATIONS.SWAP_PUMPFUN) {
    if (intent.recipient) {
      assertAddressForChain(intent.recipient, 'solana', 'recipient');
    }

    if (intent.mint) {
      assertAddressForChain(intent.mint, 'solana', 'mint');
    }

    return {
      ...base,
      action: 'swap_pumpfun',
      chain: 'solana',
      side: intent.side,
      symbol: String(intent.symbol).toUpperCase(),
      mint: intent.mint ?? null,
      amount: normalizeAmount(intent.amount, 'amount'),
      amountType: intent.amountType ?? 'quote',
      slippageBps: intent.slippageBps != null ? String(intent.slippageBps) : undefined,
      recipient: intent.recipient ?? null
    };
  }

  if (operation === EXECUTION_OPERATIONS.DEFI_DEPOSIT || operation === EXECUTION_OPERATIONS.DEFI_WITHDRAW) {
    if (intent.recipient) {
      assertAddressForChain(intent.recipient, intent.chain, 'recipient');
    }

    return {
      ...base,
      action: operation === EXECUTION_OPERATIONS.DEFI_DEPOSIT ? 'defi_deposit' : 'defi_withdraw',
      chain: intent.chain,
      protocol: String(intent.protocol).toLowerCase(),
      target: String(intent.target),
      asset: normalizeAsset(intent.asset),
      amount: normalizeAmount(intent.amount, 'amount'),
      recipient: intent.recipient ?? null,
      minSharesOut: normalizePositiveOptional(intent.minSharesOut, 'minSharesOut'),
      amountType: intent.amountType ?? 'asset'
    };
  }

  throw new OperatorError('EXECUTION_OPERATION_UNKNOWN', `Operação não reconhecida: ${operation}`);
}

export function parseExecutionPayload(payload) {
  if (!validateEnvelope(payload)) {
    throw new OperatorError('EXECUTION_SCHEMA_INVALID', 'Envelope execution plane inválido', {
      errors: formatAjvErrors(validateEnvelope.errors)
    });
  }

  const operationValidator = validateByOperation[payload.operation];
  if (!operationValidator) {
    throw new OperatorError('EXECUTION_OPERATION_UNKNOWN', `Operação não suportada: ${payload.operation}`);
  }

  if (!operationValidator(payload.intent)) {
    throw new OperatorError('EXECUTION_SCHEMA_INVALID', 'Intent execution plane inválido', {
      operation: payload.operation,
      errors: formatAjvErrors(operationValidator.errors)
    });
  }

  const requestId = ensureMachineId(payload.requestId, 'requestId');
  const correlationId = ensureMachineId(payload.correlationId, 'correlationId');

  const mentionDelegation = normalizeMentionDelegationMeta(payload.meta);
  const canonicalIntent = stripUndefined(normalizeIntent(payload.operation, payload.intent));

  return {
    envelope: {
      schemaVersion: payload.schemaVersion,
      plane: payload.plane,
      operation: payload.operation,
      requestId,
      correlationId,
      idempotencyKey: payload.idempotencyKey ?? null,
      timestamp: payload.timestamp ?? new Date().toISOString(),
      dryRun: payload.dryRun,
      meta: payload.meta ?? {},
      mentionDelegation,
      auth: payload.auth ?? null
    },
    canonicalIntent
  };
}
