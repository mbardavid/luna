import { OperatorError } from '../utils/errors.mjs';
import { SolanaConnector } from './solana.mjs';
import { decimalToAtomic, resolveSolanaToken } from './token-registry.mjs';

const DEFAULT_RAYDIUM_API_URL = 'https://transaction-v1.raydium.io';
const DEFAULT_RAYDIUM_COMPUTE_UNIT_PRICE_MICRO_LAMPORTS = 'auto';

function safeParseJson(text) {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

async function readJsonResponse(response, connector) {
  const text = await response.text();
  const parsed = safeParseJson(text);

  if (!response.ok) {
    throw new OperatorError(`${connector}_HTTP_ERROR`, `${connector} erro HTTP ${response.status}`, {
      status: response.status,
      body: parsed ?? text
    });
  }

  return parsed ?? text;
}

function extractRaydiumData(response) {
  if (response == null) return null;

  if (Array.isArray(response)) return response;
  if (response.data != null) return response.data;

  return response;
}

function extractTransactions(payload) {
  const rows = Array.isArray(payload)
    ? payload
    : Array.isArray(payload?.data)
      ? payload.data
      : Array.isArray(payload?.transactions)
        ? payload.transactions
        : [];

  return rows
    .map((row) => row?.transaction ?? row?.tx ?? row?.rawTx ?? null)
    .filter((tx) => typeof tx === 'string' && tx.length > 0);
}

function resolveComputeUnitPriceMicroLamports(rawValue) {
  const normalized = String(rawValue ?? '').trim();

  if (!normalized) return DEFAULT_RAYDIUM_COMPUTE_UNIT_PRICE_MICRO_LAMPORTS;

  const lowered = normalized.toLowerCase();
  if (lowered === 'auto') return 'auto';

  const parsed = Number(normalized);
  if (!Number.isFinite(parsed) || parsed < 0) {
    throw new OperatorError('RAYDIUM_COMPUTE_UNIT_PRICE_INVALID', 'computeUnitPriceMicroLamports inválido', {
      computeUnitPriceMicroLamports: rawValue
    });
  }

  return Math.trunc(parsed);
}

function normalizeText(value) {
  if (value == null) return '';

  if (typeof value === 'string') return value.toLowerCase();
  if (typeof value === 'number' || typeof value === 'boolean') return String(value).toLowerCase();

  return JSON.stringify(value).toLowerCase();
}

function isComputeUnitPriceRequestError(error) {
  const haystack = [
    error?.message,
    error?.code,
    error?.details?.status,
    normalizeText(error?.details?.body),
    normalizeText(error?.details?.message)
  ]
    .filter(Boolean)
    .join(' ');

  return (
    haystack.includes('req_compute_unit_price_micro_lamports_error') ||
    haystack.includes('compute_unit_price_micro_lamports') ||
    haystack.includes('computeunitpricemicrolamports')
  );
}

function buildPayload({
  quoteRaw,
  signerAddress,
  includeComputeUnitPrice,
  serializeQuote,
  computeUnitPriceMicroLamports
}) {
  return {
    txVersion: 'V0',
    wallet: signerAddress,
    wrapSol: true,
    unwrapSol: true,
    ...(includeComputeUnitPrice && computeUnitPriceMicroLamports != null
      ? { computeUnitPriceMicroLamports }
      : {}),
    swapResponse: serializeQuote ? JSON.stringify(quoteRaw) : quoteRaw
  };
}

export class RaydiumConnector {
  constructor({ apiUrl, solana } = {}) {
    this.apiUrl = apiUrl ?? process.env.RAYDIUM_API_URL ?? DEFAULT_RAYDIUM_API_URL;
    this.solana = solana ?? new SolanaConnector({});
    this.computeUnitPriceMicroLamports = resolveComputeUnitPriceMicroLamports(
      process.env.RAYDIUM_COMPUTE_UNIT_PRICE_MICRO_LAMPORTS
    );
  }

  async get(path, query) {
    const url = new URL(`${this.apiUrl}${path}`);
    Object.entries(query ?? {}).forEach(([key, value]) => {
      if (value != null && value !== '') {
        url.searchParams.set(key, String(value));
      }
    });

    const response = await fetch(url.toString(), {
      method: 'GET',
      headers: {
        accept: 'application/json'
      }
    });

    return readJsonResponse(response, 'RAYDIUM');
  }

  async post(path, payload) {
    const response = await fetch(`${this.apiUrl}${path}`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        accept: 'application/json'
      },
      body: JSON.stringify(payload)
    });

    return readJsonResponse(response, 'RAYDIUM');
  }

  resolveAssets(intent) {
    const inAsset = resolveSolanaToken(intent.assetIn, {
      field: 'inAsset',
      errorCode: 'RAYDIUM_IN_ASSET_UNSUPPORTED'
    });
    const outAsset = resolveSolanaToken(intent.assetOut, {
      field: 'outAsset',
      errorCode: 'RAYDIUM_OUT_ASSET_UNSUPPORTED'
    });

    if (inAsset.mint === outAsset.mint) {
      throw new OperatorError('RAYDIUM_PAIR_INVALID', 'Par de swap inválido (mesmo token de entrada/saída)');
    }

    return { inAsset, outAsset };
  }

  resolveMode(intent) {
    const raw = intent.mode ?? 'ExactIn';
    if (!['ExactIn', 'ExactOut'].includes(raw)) {
      throw new OperatorError('RAYDIUM_MODE_INVALID', 'mode inválido para Raydium (use ExactIn|ExactOut)', {
        mode: intent.mode
      });
    }
    return raw;
  }

  resolveRecipient(intent) {
    if (!intent.recipient) return null;
    return this.solana.ensureRecipientAddress(intent.recipient, 'recipient');
  }

  resolveSlippage(intent) {
    if (intent.slippageBps == null) return 100;

    const value = Number(intent.slippageBps);
    if (!Number.isFinite(value) || value < 0 || value > 10000) {
      throw new OperatorError('RAYDIUM_SLIPPAGE_INVALID', 'slippageBps inválido para Raydium', {
        slippageBps: intent.slippageBps
      });
    }

    return Math.round(value);
  }

  ensureRecipientCompatibility(recipient) {
    if (!recipient) return;

    const signerAddress = this.solana.getAddress();
    if (!signerAddress) return;

    if (recipient !== signerAddress) {
      throw new OperatorError(
        'RAYDIUM_RECIPIENT_UNSUPPORTED',
        'Raydium live atual requer recipient igual à wallet signer.',
        {
          recipient,
          signerAddress
        }
      );
    }
  }

  async quoteSwap(intent) {
    const mode = this.resolveMode(intent);
    const slippageBps = this.resolveSlippage(intent);
    const { inAsset, outAsset } = this.resolveAssets(intent);

    const amountAtomic = decimalToAtomic(
      intent.amount,
      mode === 'ExactIn' ? inAsset.decimals : outAsset.decimals,
      {
        field: 'amount',
        errorCode: 'RAYDIUM_AMOUNT_INVALID'
      }
    );

    const endpoint = mode === 'ExactIn' ? '/compute/swap-base-in' : '/compute/swap-base-out';

    const response = await this.get(endpoint, {
      inputMint: inAsset.mint,
      outputMint: outAsset.mint,
      amount: amountAtomic,
      slippageBps,
      txVersion: 'V0',
      poolId: intent.routeHint ?? undefined,
      onlyDirect: intent.routeHint ? 'true' : undefined
    });

    const quote = extractRaydiumData(response);

    if (!quote || typeof quote !== 'object') {
      throw new OperatorError('RAYDIUM_QUOTE_INVALID', 'Resposta de quote inválida da Raydium', {
        response
      });
    }

    return {
      mode,
      slippageBps,
      inAsset,
      outAsset,
      amountAtomic,
      quote,
      raw: response
    };
  }

  async buildTransactions({ mode, quoteRaw, signerAddress }) {
    const endpoint = mode === 'ExactIn' ? '/transaction/swap-base-in' : '/transaction/swap-base-out';
    const hasComputeUnitPrice = this.computeUnitPriceMicroLamports != null;

    const attemptPlans = [
      { includeComputeUnitPrice: true, serializeQuote: false },
      { includeComputeUnitPrice: true, serializeQuote: true },
      { includeComputeUnitPrice: false, serializeQuote: false },
      { includeComputeUnitPrice: false, serializeQuote: true }
    ].filter(({ includeComputeUnitPrice }) => includeComputeUnitPrice ? hasComputeUnitPrice : true);

    let lastResponse = null;

    for (const attempt of attemptPlans) {
      const payload = buildPayload({
        quoteRaw,
        signerAddress,
        includeComputeUnitPrice: attempt.includeComputeUnitPrice,
        computeUnitPriceMicroLamports: this.computeUnitPriceMicroLamports,
        serializeQuote: attempt.serializeQuote
      });

      try {
        const response = await this.post(endpoint, payload);
        const transactions = extractTransactions(response);
        lastResponse = response;

        if (transactions.length > 0) {
          return {
            transactions,
            raw: response
          };
        }
      } catch (error) {
        if (!(error instanceof OperatorError)) throw error;
        if (attempt.includeComputeUnitPrice && isComputeUnitPriceRequestError(error)) {
          continue;
        }

        throw error;
      }
    }

    throw new OperatorError('RAYDIUM_SWAP_TX_MISSING', 'Raydium não retornou transações serializadas', {
      response: lastResponse
    });
  }

  async preflightSwap(intent) {
    try {
      const recipient = this.resolveRecipient(intent);
      const quote = await this.quoteSwap(intent);
      const signerAddress = this.solana.getAddress();

      this.ensureRecipientCompatibility(recipient);

      const base = {
        chain: 'solana',
        connector: 'raydium',
        action: 'swap_raydium',
        mode: quote.mode,
        inAsset: quote.inAsset.symbol,
        outAsset: quote.outAsset.symbol,
        inMint: quote.inAsset.mint,
        outMint: quote.outAsset.mint,
        requestedAmount: String(intent.amount),
        requestedAmountAtomic: quote.amountAtomic,
        slippageBps: quote.slippageBps,
        poolId: intent.routeHint ?? null,
        quote,
        recipient: recipient ?? signerAddress ?? null,
        walletReady: Boolean(signerAddress)
      };

      if (!signerAddress) {
        return {
          ...base,
          note: 'SOLANA_PRIVATE_KEY ausente: dry-run sem simulação da transação assinada.'
        };
      }

      const built = await this.buildTransactions({
        mode: quote.mode,
        quoteRaw: quote.raw,
        signerAddress
      });

      const simulation = await this.solana.preflightSerializedTransaction({
        transactionBase64: built.transactions[0],
        label: 'raydium.swap'
      });

      return {
        ...base,
        buildCount: built.transactions.length,
        simulation
      };
    } catch (error) {
      if (error instanceof OperatorError) throw error;
      throw new OperatorError('RAYDIUM_PREFLIGHT_FAILED', 'Falha no preflight do swap Raydium', {
        message: error.message
      });
    }
  }

  async executeSwap(intent, context = {}) {
    try {
      this.solana.ensureWallet();

      const recipient = this.resolveRecipient(intent);
      this.ensureRecipientCompatibility(recipient);

      const quote = await this.quoteSwap(intent);
      const signerAddress = this.solana.getAddress();

      const built = await this.buildTransactions({
        mode: quote.mode,
        quoteRaw: quote.raw,
        signerAddress
      });

      const executions = [];

      for (const [index, transactionBase64] of built.transactions.entries()) {
        const label = `raydium.swap.${index + 1}`;

        const preflight = await this.solana.preflightSerializedTransaction({
          transactionBase64,
          label
        });

        const execution = await this.solana.sendSerializedTransaction({
          transactionBase64,
          label,
          skipPreflight: false,
          maxRetries: 3
        });

        executions.push({ preflight, execution });
      }

      return {
        chain: 'solana',
        connector: 'raydium',
        action: 'swap_raydium',
        idempotencyKey: context.idempotencyKey ?? null,
        quote: {
          mode: quote.mode,
          inAsset: quote.inAsset.symbol,
          outAsset: quote.outAsset.symbol,
          inMint: quote.inAsset.mint,
          outMint: quote.outAsset.mint,
          requestedAmount: String(intent.amount),
          requestedAmountAtomic: quote.amountAtomic,
          slippageBps: quote.slippageBps,
          poolId: intent.routeHint ?? null
        },
        txCount: built.transactions.length,
        recipient: recipient ?? signerAddress,
        executions
      };
    } catch (error) {
      if (error instanceof OperatorError) throw error;
      throw new OperatorError('RAYDIUM_EXECUTION_FAILED', 'Falha no swap live Raydium', {
        message: error.message
      });
    }
  }
}
