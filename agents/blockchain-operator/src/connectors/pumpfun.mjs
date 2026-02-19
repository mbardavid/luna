import { OperatorError } from '../utils/errors.mjs';
import { SolanaConnector } from './solana.mjs';
import { maybeResolvePumpfunMintBySymbol, resolveSolanaToken } from './token-registry.mjs';

const DEFAULT_METADATA_API_URL = 'https://frontend-api.pump.fun';
const DEFAULT_TRADE_API_URL = 'https://pumpportal.fun/api/trade-local';
const SOLANA_MINT = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;

function safeJsonParse(value) {
  if (!value) return null;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

async function readJsonResponse(response, connector) {
  const text = await response.text();
  const parsed = safeJsonParse(text);

  if (!response.ok) {
    throw new OperatorError(`${connector}_HTTP_ERROR`, `${connector} erro HTTP ${response.status}`, {
      status: response.status,
      body: parsed ?? text
    });
  }

  return parsed ?? text;
}

function parseTransactionCandidate(value) {
  if (!value) return null;

  if (typeof value === 'string') {
    const trimmed = value.trim();
    return trimmed.length > 0 ? trimmed : null;
  }

  if (Array.isArray(value) && value.every((item) => Number.isInteger(item) && item >= 0 && item <= 255)) {
    return Buffer.from(value).toString('base64');
  }

  return null;
}

export class PumpfunConnector {
  constructor({ metadataApiUrl, tradeApiUrl, solana } = {}) {
    this.metadataApiUrl = metadataApiUrl ?? process.env.PUMPFUN_API_URL ?? DEFAULT_METADATA_API_URL;
    this.tradeApiUrl = tradeApiUrl ?? process.env.PUMPFUN_TRADE_API_URL ?? DEFAULT_TRADE_API_URL;
    this.tradeApiKey = process.env.PUMPFUN_TRADE_API_KEY ?? null;
    this.priorityFeeSol = Number(process.env.PUMPFUN_PRIORITY_FEE_SOL ?? '0.0005');
    this.solana = solana ?? new SolanaConnector({});
  }

  resolveSide(intent) {
    const side = String(intent.side ?? '').toLowerCase();
    if (!['buy', 'sell'].includes(side)) {
      throw new OperatorError('PUMPFUN_SIDE_INVALID', 'side inválido para Pump.fun (use buy|sell)', {
        side: intent.side
      });
    }
    return side;
  }

  resolveAmountType(intent) {
    const amountType = intent.amountType ? String(intent.amountType) : 'quote';
    if (!['base', 'quote'].includes(amountType)) {
      throw new OperatorError('PUMPFUN_AMOUNT_TYPE_INVALID', 'amountType inválido para Pump.fun', {
        amountType
      });
    }

    return amountType;
  }

  resolveRecipient(intent) {
    if (!intent.recipient) return null;
    return this.solana.ensureRecipientAddress(intent.recipient, 'recipient');
  }

  ensureRecipientCompatibility(recipient) {
    if (!recipient) return;

    const signerAddress = this.solana.getAddress();
    if (!signerAddress) return;

    if (recipient !== signerAddress) {
      throw new OperatorError(
        'PUMPFUN_RECIPIENT_UNSUPPORTED',
        'Pump.fun live atual requer recipient igual à wallet signer.',
        {
          recipient,
          signerAddress
        }
      );
    }
  }

  resolveMint(intent) {
    if (intent.mint && SOLANA_MINT.test(intent.mint)) {
      return intent.mint;
    }

    const bySymbol = maybeResolvePumpfunMintBySymbol(intent.symbol);
    if (bySymbol) return bySymbol;

    throw new OperatorError(
      'PUMPFUN_MINT_REQUIRED',
      'mint é obrigatório para Pump.fun (ou configure PUMPFUN_SYMBOL_MAP_JSON).',
      {
        symbol: intent.symbol,
        mint: intent.mint ?? null
      }
    );
  }

  resolveSlippageBps(intent) {
    if (intent.slippageBps == null) return 250;

    const value = Number(intent.slippageBps);
    if (!Number.isFinite(value) || value < 0 || value > 10000) {
      throw new OperatorError('PUMPFUN_SLIPPAGE_INVALID', 'slippageBps inválido para Pump.fun', {
        slippageBps: intent.slippageBps
      });
    }

    return Math.round(value);
  }

  async fetchCoinMetadata(mint) {
    try {
      const response = await fetch(`${this.metadataApiUrl}/coins/${mint}`, {
        method: 'GET',
        headers: {
          accept: 'application/json'
        }
      });

      return await readJsonResponse(response, 'PUMPFUN');
    } catch (error) {
      if (error instanceof OperatorError) {
        return {
          warning: {
            code: error.code,
            message: error.message
          }
        };
      }

      return {
        warning: {
          code: 'PUMPFUN_METADATA_UNAVAILABLE',
          message: error.message
        }
      };
    }
  }

  buildTradePayload({ intent, mint, signerAddress }) {
    const amount = Number(intent.amount);
    if (!Number.isFinite(amount) || amount <= 0) {
      throw new OperatorError('PUMPFUN_AMOUNT_INVALID', 'amount inválido para Pump.fun', {
        amount: intent.amount
      });
    }

    const amountType = this.resolveAmountType(intent);
    const slippageBps = this.resolveSlippageBps(intent);

    const quoteAsset = amountType === 'quote' ? String(intent.quoteAsset ?? 'SOL').toUpperCase() : null;
    if (quoteAsset && quoteAsset !== 'SOL') {
      throw new OperatorError(
        'PUMPFUN_QUOTE_ASSET_UNSUPPORTED',
        'Pump.fun live atual suporta amountType=quote apenas em SOL.',
        {
          quoteAsset
        }
      );
    }

    return {
      publicKey: signerAddress,
      action: this.resolveSide(intent),
      mint,
      amount,
      denominatedInSol: amountType === 'quote',
      slippage: slippageBps / 100,
      priorityFee: this.priorityFeeSol,
      pool: process.env.PUMPFUN_POOL ?? 'pump',
      amountType,
      slippageBps,
      quoteAsset
    };
  }

  async requestTradeTransaction(payload) {
    const response = await fetch(this.tradeApiUrl, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        accept: 'application/json,application/octet-stream',
        ...(this.tradeApiKey ? { 'x-api-key': this.tradeApiKey } : {})
      },
      body: JSON.stringify({
        publicKey: payload.publicKey,
        action: payload.action,
        mint: payload.mint,
        amount: payload.amount,
        denominatedInSol: payload.denominatedInSol,
        slippage: payload.slippage,
        priorityFee: payload.priorityFee,
        pool: payload.pool
      })
    });

    if (!response.ok) {
      const text = await response.text();
      const parsed = safeJsonParse(text);
      throw new OperatorError('PUMPFUN_HTTP_ERROR', `Pump.fun erro HTTP ${response.status}`, {
        status: response.status,
        body: parsed ?? text
      });
    }

    const contentType = String(response.headers.get('content-type') ?? '').toLowerCase();

    if (contentType.includes('application/json') || contentType.includes('text/')) {
      const text = await response.text();
      const parsed = safeJsonParse(text);

      const tx = parseTransactionCandidate(
        parsed?.transaction ?? parsed?.tx ?? parsed?.serializedTransaction ?? parsed?.data
      );

      if (!tx) {
        throw new OperatorError('PUMPFUN_SWAP_TX_MISSING', 'Pump.fun não retornou transação serializada', {
          response: parsed ?? text
        });
      }

      return {
        transactionBase64: tx,
        raw: parsed ?? text
      };
    }

    const binary = Buffer.from(await response.arrayBuffer());
    if (!binary || binary.length === 0) {
      throw new OperatorError('PUMPFUN_SWAP_TX_EMPTY', 'Pump.fun retornou payload vazio para transação');
    }

    return {
      transactionBase64: binary.toString('base64'),
      raw: {
        contentType,
        bytes: binary.length
      }
    };
  }

  async preflightTrade(intent) {
    try {
      const mint = this.resolveMint(intent);
      const token = resolveSolanaToken(mint, {
        field: 'mint',
        errorCode: 'PUMPFUN_MINT_INVALID',
        defaultDecimals: 6
      });

      const recipient = this.resolveRecipient(intent);
      this.ensureRecipientCompatibility(recipient);

      const signerAddress = this.solana.getAddress();
      const metadata = await this.fetchCoinMetadata(mint);

      const base = {
        chain: 'solana',
        connector: 'pumpfun',
        action: 'swap_pumpfun',
        side: this.resolveSide(intent),
        symbol: String(intent.symbol).toUpperCase(),
        mint: token.mint,
        amount: String(intent.amount),
        amountType: this.resolveAmountType(intent),
        slippageBps: this.resolveSlippageBps(intent),
        recipient: recipient ?? signerAddress ?? null,
        walletReady: Boolean(signerAddress),
        metadata
      };

      if (!signerAddress) {
        return {
          ...base,
          note: 'SOLANA_PRIVATE_KEY ausente: dry-run sem simulação da transação assinada.'
        };
      }

      const tradePayload = this.buildTradePayload({ intent, mint: token.mint, signerAddress });
      const built = await this.requestTradeTransaction(tradePayload);
      const simulation = await this.solana.preflightSerializedTransaction({
        transactionBase64: built.transactionBase64,
        label: 'pumpfun.trade'
      });

      return {
        ...base,
        tradePayload: {
          action: tradePayload.action,
          amount: tradePayload.amount,
          denominatedInSol: tradePayload.denominatedInSol,
          priorityFee: tradePayload.priorityFee,
          pool: tradePayload.pool
        },
        simulation
      };
    } catch (error) {
      if (error instanceof OperatorError) throw error;
      throw new OperatorError('PUMPFUN_PREFLIGHT_FAILED', 'Falha no preflight do trade Pump.fun', {
        message: error.message
      });
    }
  }

  async executeTrade(intent, context = {}) {
    try {
      this.solana.ensureWallet();

      const mint = this.resolveMint(intent);
      const recipient = this.resolveRecipient(intent);
      this.ensureRecipientCompatibility(recipient);

      const signerAddress = this.solana.getAddress();
      const tradePayload = this.buildTradePayload({ intent, mint, signerAddress });
      const built = await this.requestTradeTransaction(tradePayload);

      const preflight = await this.solana.preflightSerializedTransaction({
        transactionBase64: built.transactionBase64,
        label: 'pumpfun.trade'
      });

      const execution = await this.solana.sendSerializedTransaction({
        transactionBase64: built.transactionBase64,
        label: 'pumpfun.trade',
        skipPreflight: false,
        maxRetries: 3
      });

      return {
        chain: 'solana',
        connector: 'pumpfun',
        action: 'swap_pumpfun',
        idempotencyKey: context.idempotencyKey ?? null,
        symbol: String(intent.symbol).toUpperCase(),
        mint,
        amount: String(intent.amount),
        amountType: tradePayload.amountType,
        side: tradePayload.action,
        recipient: recipient ?? signerAddress,
        slippageBps: tradePayload.slippageBps,
        preflight,
        execution
      };
    } catch (error) {
      if (error instanceof OperatorError) throw error;
      throw new OperatorError('PUMPFUN_EXECUTION_FAILED', 'Falha no trade live Pump.fun', {
        message: error.message
      });
    }
  }
}
