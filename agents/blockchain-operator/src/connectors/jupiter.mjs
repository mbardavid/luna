import { OperatorError } from '../utils/errors.mjs';
import { SolanaConnector } from './solana.mjs';
import { decimalToAtomic, resolveSolanaToken } from './token-registry.mjs';

const DEFAULT_JUPITER_API_URL = 'https://lite-api.jup.ag';
const DEFAULT_RETRY_ATTEMPTS = 3;
const DEFAULT_RETRY_BASE_MS = 400;

function isObject(value) {
  return value != null && typeof value === 'object' && !Array.isArray(value);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function shouldRetryHttpStatus(status) {
  return status === 408 || status === 425 || status === 429 || (status >= 500 && status <= 599);
}

function isRetryableNetworkError(error) {
  const message = String(error?.message ?? '').toLowerCase();
  return (
    error?.name === 'TypeError' ||
    message.includes('fetch failed') ||
    message.includes('network') ||
    message.includes('timed out') ||
    message.includes('econnreset') ||
    message.includes('enotfound') ||
    message.includes('eai_again')
  );
}

async function readJsonResponse(response, connector) {
  const text = await response.text();

  let parsed = null;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch {
    parsed = null;
  }

  if (!response.ok) {
    throw new OperatorError(`${connector}_HTTP_ERROR`, `${connector} erro HTTP ${response.status}`, {
      status: response.status,
      body: parsed ?? text
    });
  }

  if (!isObject(parsed)) {
    throw new OperatorError(`${connector}_RESPONSE_INVALID`, `${connector} retornou payload não JSON`, {
      status: response.status,
      body: text
    });
  }

  return parsed;
}

export class JupiterConnector {
  constructor({ apiUrl, solana, retryAttempts, retryBaseMs } = {}) {
    const configuredApiUrl = apiUrl ?? process.env.JUPITER_API_URL ?? DEFAULT_JUPITER_API_URL;

    // Backward compatibility: old deployments still point to quote-api.jup.ag/v6.
    // Jupiter currently serves stable swap endpoints via lite-api.jup.ag.
    // Keep explicit constructor overrides untouched (used by tests/mocks).
    this.apiUrl =
      apiUrl == null
        ? String(configuredApiUrl).replace('https://quote-api.jup.ag/v6', 'https://lite-api.jup.ag')
        : String(configuredApiUrl);

    this.solana = solana ?? new SolanaConnector({});

    this.retryAttempts = Number(
      retryAttempts ?? process.env.JUPITER_RETRY_ATTEMPTS ?? DEFAULT_RETRY_ATTEMPTS
    );
    this.retryBaseMs = Number(retryBaseMs ?? process.env.JUPITER_RETRY_BASE_MS ?? DEFAULT_RETRY_BASE_MS);
  }

  resolveQuotePath() {
    return this.apiUrl.includes('/v6') ? '/quote' : '/swap/v1/quote';
  }

  resolveSwapPath() {
    return this.apiUrl.includes('/v6') ? '/swap' : '/swap/v1/swap';
  }

  async requestJson(path, { method, query, payload }) {
    const url = new URL(`${this.apiUrl}${path}`);
    Object.entries(query ?? {}).forEach(([key, value]) => {
      if (value != null && value !== '') {
        url.searchParams.set(key, String(value));
      }
    });

    const headers = {
      accept: 'application/json'
    };

    const options = {
      method,
      headers
    };

    if (payload != null) {
      headers['content-type'] = 'application/json';
      options.body = JSON.stringify(payload);
    }

    const maxAttempts = Number.isFinite(this.retryAttempts) && this.retryAttempts > 0 ? Math.floor(this.retryAttempts) : 1;
    let lastError = null;

    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
      try {
        const response = await fetch(url.toString(), options);

        if (!response.ok && shouldRetryHttpStatus(response.status) && attempt < maxAttempts) {
          await sleep(this.retryBaseMs * attempt);
          continue;
        }

        return readJsonResponse(response, 'JUPITER');
      } catch (error) {
        lastError = error;

        if (!isRetryableNetworkError(error) || attempt >= maxAttempts) {
          throw error;
        }

        await sleep(this.retryBaseMs * attempt);
      }
    }

    throw lastError ?? new Error('Jupiter request failed after retries');
  }

  async get(path, query) {
    return this.requestJson(path, { method: 'GET', query });
  }

  async post(path, payload) {
    return this.requestJson(path, { method: 'POST', payload });
  }

  resolveAssets(intent) {
    const inAsset = resolveSolanaToken(intent.assetIn, {
      field: 'inAsset',
      errorCode: 'JUPITER_IN_ASSET_UNSUPPORTED'
    });
    const outAsset = resolveSolanaToken(intent.assetOut, {
      field: 'outAsset',
      errorCode: 'JUPITER_OUT_ASSET_UNSUPPORTED'
    });

    if (inAsset.mint === outAsset.mint) {
      throw new OperatorError('JUPITER_PAIR_INVALID', 'Par de swap inválido (mesmo token de entrada/saída)');
    }

    return { inAsset, outAsset };
  }

  normalizeMode(mode) {
    if (!mode) return 'ExactIn';
    const normalized = String(mode);

    if (!['ExactIn', 'ExactOut'].includes(normalized)) {
      throw new OperatorError('JUPITER_MODE_INVALID', 'mode inválido para Jupiter (use ExactIn|ExactOut)', {
        mode
      });
    }

    return normalized;
  }

  resolveSlippage(intent) {
    if (intent.slippageBps == null) return 100;

    const value = Number(intent.slippageBps);
    if (!Number.isFinite(value) || value < 0 || value > 10000) {
      throw new OperatorError('JUPITER_SLIPPAGE_INVALID', 'slippageBps inválido para Jupiter', {
        slippageBps: intent.slippageBps
      });
    }

    return Math.round(value);
  }

  resolveRecipient(intent) {
    if (!intent.recipient) return null;
    return this.solana.ensureRecipientAddress(intent.recipient, 'recipient');
  }

  async quoteSwap(intent) {
    const mode = this.normalizeMode(intent.mode);
    const slippageBps = this.resolveSlippage(intent);
    const { inAsset, outAsset } = this.resolveAssets(intent);
    const amountAtomic = decimalToAtomic(
      intent.amount,
      mode === 'ExactIn' ? inAsset.decimals : outAsset.decimals,
      {
        field: 'amount',
        errorCode: 'JUPITER_AMOUNT_INVALID'
      }
    );

    const quote = await this.get(this.resolveQuotePath(), {
      inputMint: inAsset.mint,
      outputMint: outAsset.mint,
      amount: amountAtomic,
      slippageBps,
      swapMode: mode,
      onlyDirectRoutes: intent.routeHint ? 'true' : undefined,
      routeHint: intent.routeHint ?? undefined
    });

    if (!isObject(quote) || !quote.inAmount || !quote.outAmount) {
      throw new OperatorError('JUPITER_QUOTE_INVALID', 'Resposta de quote inválida da Jupiter', {
        quote
      });
    }

    return {
      quote,
      mode,
      slippageBps,
      inAsset,
      outAsset,
      amountAtomic
    };
  }

  ensureRecipientCompatibility(recipient) {
    if (!recipient) return;

    const signerAddress = this.solana.getAddress();
    if (!signerAddress) return;

    if (recipient !== signerAddress) {
      throw new OperatorError(
        'JUPITER_RECIPIENT_UNSUPPORTED',
        'Jupiter live atual requer recipient igual à wallet signer para evitar perda de fundos.',
        {
          recipient,
          signerAddress
        }
      );
    }
  }

  async buildSwapTx({ quote, signerAddress }) {
    const payload = {
      quoteResponse: quote,
      userPublicKey: signerAddress,
      wrapAndUnwrapSol: true,
      dynamicComputeUnitLimit: true,
      asLegacyTransaction: false
    };

    const response = await this.post(this.resolveSwapPath(), payload);

    const swapTransaction =
      response?.swapTransaction ?? response?.data?.swapTransaction ?? response?.tx ?? null;

    if (!swapTransaction || typeof swapTransaction !== 'string') {
      throw new OperatorError('JUPITER_SWAP_TX_MISSING', 'Jupiter não retornou swapTransaction serializada', {
        response
      });
    }

    return {
      swapTransaction,
      meta: {
        lastValidBlockHeight: response?.lastValidBlockHeight ?? null,
        prioritizationFeeLamports: response?.prioritizationFeeLamports ?? null,
        computeUnitLimit: response?.computeUnitLimit ?? null
      },
      raw: response
    };
  }

  async preflightSwap(intent) {
    try {
      const recipient = this.resolveRecipient(intent);
      const quoted = await this.quoteSwap(intent);
      const signerAddress = this.solana.getAddress();

      this.ensureRecipientCompatibility(recipient);

      const base = {
        chain: 'solana',
        connector: 'jupiter',
        action: 'swap_jupiter',
        mode: quoted.mode,
        routeHint: intent.routeHint ?? null,
        inAsset: quoted.inAsset.symbol,
        outAsset: quoted.outAsset.symbol,
        inMint: quoted.inAsset.mint,
        outMint: quoted.outAsset.mint,
        requestedAmount: String(intent.amount),
        requestedAmountAtomic: quoted.amountAtomic,
        quotedInAmountAtomic: String(quoted.quote.inAmount),
        quotedOutAmountAtomic: String(quoted.quote.outAmount),
        priceImpactPct: quoted.quote.priceImpactPct ?? null,
        slippageBps: quoted.slippageBps,
        routePlanSize: Array.isArray(quoted.quote.routePlan) ? quoted.quote.routePlan.length : null,
        recipient: recipient ?? signerAddress ?? null,
        walletReady: Boolean(signerAddress)
      };

      if (!signerAddress) {
        return {
          ...base,
          note: 'SOLANA_PRIVATE_KEY ausente: dry-run sem simulação da transação assinada.'
        };
      }

      const built = await this.buildSwapTx({
        quote: quoted.quote,
        signerAddress
      });

      const simulation = await this.solana.preflightSerializedTransaction({
        transactionBase64: built.swapTransaction,
        label: 'jupiter.swap'
      });

      return {
        ...base,
        simulation,
        buildMeta: built.meta
      };
    } catch (error) {
      if (error instanceof OperatorError) throw error;
      throw new OperatorError('JUPITER_PREFLIGHT_FAILED', 'Falha no preflight do swap Jupiter', {
        message: error.message
      });
    }
  }

  async executeSwap(intent, context = {}) {
    try {
      this.solana.ensureWallet();

      const recipient = this.resolveRecipient(intent);
      this.ensureRecipientCompatibility(recipient);

      const quoted = await this.quoteSwap(intent);
      const signerAddress = this.solana.getAddress();

      const built = await this.buildSwapTx({
        quote: quoted.quote,
        signerAddress
      });

      const preflight = await this.solana.preflightSerializedTransaction({
        transactionBase64: built.swapTransaction,
        label: 'jupiter.swap'
      });

      const execution = await this.solana.sendSerializedTransaction({
        transactionBase64: built.swapTransaction,
        label: 'jupiter.swap',
        skipPreflight: false,
        maxRetries: 3
      });

      return {
        chain: 'solana',
        connector: 'jupiter',
        action: 'swap_jupiter',
        idempotencyKey: context.idempotencyKey ?? null,
        preflight,
        quote: {
          mode: quoted.mode,
          inAsset: quoted.inAsset.symbol,
          outAsset: quoted.outAsset.symbol,
          inMint: quoted.inAsset.mint,
          outMint: quoted.outAsset.mint,
          requestedAmount: String(intent.amount),
          requestedAmountAtomic: quoted.amountAtomic,
          quotedInAmountAtomic: String(quoted.quote.inAmount),
          quotedOutAmountAtomic: String(quoted.quote.outAmount),
          slippageBps: quoted.slippageBps,
          priceImpactPct: quoted.quote.priceImpactPct ?? null,
          routePlanSize: Array.isArray(quoted.quote.routePlan) ? quoted.quote.routePlan.length : null
        },
        buildMeta: built.meta,
        recipient: recipient ?? signerAddress,
        execution
      };
    } catch (error) {
      if (error instanceof OperatorError) throw error;
      throw new OperatorError('JUPITER_EXECUTION_FAILED', 'Falha no swap live Jupiter', {
        message: error.message
      });
    }
  }
}
