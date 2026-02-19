import { DEBRIDGE_CHAIN_IDS } from '../core/constants.mjs';
import { OperatorError } from '../utils/errors.mjs';
import { BaseConnector } from './base.mjs';
import { SolanaConnector } from './solana.mjs';
import { ArbitrumConnector } from './arbitrum.mjs';
import { decimalToAtomic, resolveArbitrumToken, resolveBaseToken, resolveSolanaToken } from './token-registry.mjs';

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function safeJsonParse(value) {
  if (!value) return null;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function normalizeAsset(value) {
  return String(value ?? '').trim().toUpperCase();
}

function pickFirst(...values) {
  for (const value of values) {
    if (value != null && value !== '') return value;
  }
  return null;
}

function parseBridgeStatus(response) {
  if (!response || typeof response !== 'object') return null;

  const statusRaw =
    response.status ??
    response.orderStatus ??
    response.result?.status ??
    response.data?.status ??
    response.txStatus ??
    null;

  if (!statusRaw) return null;
  return String(statusRaw).toLowerCase();
}

export class DebridgeConnector {
  constructor({ apiUrl, base, solana, arbitrum } = {}) {
    this.apiUrl = apiUrl ?? process.env.DEBRIDGE_API_URL ?? 'https://dln.debridge.finance/v1.0';
    this.base = base ?? new BaseConnector({});
    this.solana = solana ?? new SolanaConnector({});
    this.arbitrum = arbitrum ?? new ArbitrumConnector({});

    this.trackPollAttempts = Number(process.env.DEBRIDGE_TRACK_POLL_ATTEMPTS ?? '1');
    this.trackPollIntervalMs = Number(process.env.DEBRIDGE_TRACK_POLL_INTERVAL_MS ?? '1500');
  }

  assertHyperliquidRouteSupport(intent) {
    const touchesHyperliquid = intent.fromChain === 'hyperliquid' || intent.toChain === 'hyperliquid';
    if (!touchesHyperliquid) return;

    throw new OperatorError(
      'DEBRIDGE_HYPERLIQUID_ROUTE_NOT_SUPPORTED',
      'deBridge não executa o fluxo nativo Bridge2 do Hyperliquid. Use pipeline explícito via Arbitrum native bridge.',
      {
        requestedRoute: `${intent.fromChain}->${intent.toChain}`,
        evidence: {
          debridgeCreateTxFields: [
            'srcChainId',
            'dstChainId',
            'srcChainTokenIn',
            'dstChainTokenOut',
            'dstChainTokenOutRecipient'
          ],
          hyperliquidBridge2Docs: 'https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/bridge2',
          note:
            'Bridge2 exige transferência USDC para contrato Arbitrum 0x2df1... (deposit) ou ação withdraw3 (withdraw).'
        },
        suggestedPipeline:
          intent.toChain === 'hyperliquid'
            ? [
                `${intent.fromChain}->arbitrum via deBridge`,
                'arbitrum->hyperliquid via hyperliquid.bridge.deposit'
              ]
            : [
                'hyperliquid->arbitrum via hyperliquid.bridge.withdraw',
                `arbitrum->${intent.toChain} via deBridge`
              ]
      }
    );
  }

  resolveToken(chain, asset) {
    const normalizedAsset = normalizeAsset(asset);

    if (chain === 'base') {
      const token = resolveBaseToken(normalizedAsset, {
        field: 'asset',
        errorCode: 'DEBRIDGE_TOKEN_UNSUPPORTED'
      });

      if (token.native || !token.address) {
        throw new OperatorError(
          'DEBRIDGE_TOKEN_UNSUPPORTED',
          `Asset ${normalizedAsset} não suportada para bridge em Base (use token ERC20).`
        );
      }

      return {
        chain,
        symbol: token.symbol,
        decimals: token.decimals,
        address: token.address
      };
    }

    if (chain === 'solana') {
      const token = resolveSolanaToken(normalizedAsset, {
        field: 'asset',
        errorCode: 'DEBRIDGE_TOKEN_UNSUPPORTED'
      });

      if (token.native) {
        throw new OperatorError(
          'DEBRIDGE_TOKEN_UNSUPPORTED',
          `Asset ${normalizedAsset} não suportada para bridge em Solana (use token SPL).`
        );
      }

      return {
        chain,
        symbol: token.symbol,
        decimals: token.decimals,
        address: token.mint
      };
    }

    if (chain === 'arbitrum') {
      const token = resolveArbitrumToken(normalizedAsset, {
        field: 'asset',
        errorCode: 'DEBRIDGE_TOKEN_UNSUPPORTED'
      });

      if (token.native || !token.address) {
        throw new OperatorError(
          'DEBRIDGE_TOKEN_UNSUPPORTED',
          `Asset ${normalizedAsset} não suportada para bridge em Arbitrum (use token ERC20).`
        );
      }

      return {
        chain,
        symbol: token.symbol,
        decimals: token.decimals,
        address: token.address
      };
    }

    throw new OperatorError('DEBRIDGE_CHAIN_UNSUPPORTED', 'Chain não suportada para bridge deBridge', {
      chain
    });
  }

  async get(path, query) {
    const url = new URL(`${this.apiUrl}${path}`);
    Object.entries(query ?? {}).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') {
        url.searchParams.set(k, String(v));
      }
    });

    const response = await fetch(url.toString(), {
      method: 'GET',
      headers: { accept: 'application/json' }
    });

    const text = await response.text();
    const json = safeJsonParse(text);

    if (!response.ok) {
      throw new OperatorError('DEBRIDGE_HTTP_ERROR', `deBridge erro HTTP ${response.status}`, {
        status: response.status,
        body: json ?? text
      });
    }

    return json ?? text;
  }

  buildQuoteQuery(intent) {
    this.assertHyperliquidRouteSupport(intent);

    const srcChainId = DEBRIDGE_CHAIN_IDS[intent.fromChain];
    const dstChainId = DEBRIDGE_CHAIN_IDS[intent.toChain];

    if (!srcChainId || !dstChainId) {
      throw new OperatorError('DEBRIDGE_CHAIN_UNSUPPORTED', 'Chain não suportada para deBridge no runtime atual');
    }

    const srcToken = this.resolveToken(intent.fromChain, intent.asset);
    const dstToken = this.resolveToken(intent.toChain, intent.asset);

    const amountAtomic = decimalToAtomic(intent.amount, srcToken.decimals, {
      field: 'amount',
      errorCode: 'DEBRIDGE_AMOUNT_INVALID'
    });

    const query = {
      srcChainId,
      dstChainId,
      srcChainTokenIn: srcToken.address,
      dstChainTokenOut: dstToken.address,
      srcChainTokenInAmount: amountAtomic,
      dstChainTokenOutAmount: 'auto',
      prependOperatingExpenses: 'true'
    };

    if (intent.maxSlippageBps != null) {
      query.maxSlippageBps = String(intent.maxSlippageBps);
    }

    if (intent.quoteId) {
      query.quoteId = intent.quoteId;
    }

    if (intent.recipient) {
      query.dstChainTokenOutRecipient = intent.recipient;
      query.dstChainOrderAuthorityAddress = intent.recipient;

      if (intent.fromChain === 'solana') {
        const srcAuthority = this.solana.getAddress();
        if (srcAuthority) query.srcChainOrderAuthorityAddress = srcAuthority;
      } else if (intent.fromChain === 'base') {
        const srcAuthority = this.base.getAddress();
        if (srcAuthority) query.srcChainOrderAuthorityAddress = srcAuthority;
      } else if (intent.fromChain === 'arbitrum') {
        const srcAuthority = this.arbitrum.getAddress();
        if (srcAuthority) query.srcChainOrderAuthorityAddress = srcAuthority;
      }
    }

    return {
      srcChainId,
      dstChainId,
      srcToken,
      dstToken,
      amountAtomic,
      query
    };
  }

  async preflightBridge(intent) {
    try {
      const quote = this.buildQuoteQuery(intent);
      const response = await this.get('/dln/order/create-tx', quote.query);

      return {
        chain: 'debridge',
        connector: 'debridge',
        route: `${intent.fromChain}->${intent.toChain}`,
        fromChain: intent.fromChain,
        toChain: intent.toChain,
        asset: quote.srcToken.symbol,
        srcTokenAddress: quote.srcToken.address,
        dstTokenAddress: quote.dstToken.address,
        amount: String(intent.amount),
        amountAtomic: quote.amountAtomic,
        estimation: response?.estimation ?? null,
        hasTxPayload: Boolean(response?.tx),
        orderId: pickFirst(response?.orderId, response?.estimation?.orderId, response?.estimation?.dlOrderId),
        response
      };
    } catch (error) {
      if (error instanceof OperatorError) throw error;
      throw new OperatorError('DEBRIDGE_PREFLIGHT_FAILED', 'Falha no preflight de bridge deBridge', {
        message: error.message
      });
    }
  }

  parseSourceTransaction(preflight) {
    const tx = preflight?.response?.tx;

    if (!tx) {
      throw new OperatorError('DEBRIDGE_TX_MISSING', 'deBridge não retornou payload de transação em create-tx', {
        response: preflight?.response ?? null
      });
    }

    if (preflight.fromChain === 'base' || preflight.fromChain === 'arbitrum') {
      const to = pickFirst(tx.to, tx.toAddress, tx.target, tx.contractAddress);
      const data = pickFirst(tx.data, tx.callData, tx.calldata, tx.input);
      const valueWei = String(pickFirst(tx.value, tx.valueWei, tx.nativeValue, tx.amountInWei, '0'));
      const gasLimit = pickFirst(tx.gas, tx.gasLimit);
      const maxFeePerGasWei = pickFirst(tx.maxFeePerGas, tx.maxFeePerGasWei);
      const maxPriorityFeePerGasWei = pickFirst(tx.maxPriorityFeePerGas, tx.maxPriorityFeePerGasWei);

      if (!to || !data) {
        throw new OperatorError('DEBRIDGE_TX_PAYLOAD_INVALID', 'Payload tx EVM inválido retornado por deBridge', {
          tx
        });
      }

      return {
        chain: preflight.fromChain,
        to,
        data,
        valueWei,
        gasLimit,
        maxFeePerGasWei,
        maxPriorityFeePerGasWei,
        raw: tx
      };
    }

    if (preflight.fromChain === 'solana') {
      const serializedRaw = pickFirst(
        tx.encodedTransaction,
        tx.serializedTransaction,
        tx.transaction,
        tx.tx,
        tx.data,
        typeof tx === 'string' ? tx : null
      );

      if (!serializedRaw || typeof serializedRaw !== 'string') {
        throw new OperatorError('DEBRIDGE_TX_PAYLOAD_INVALID', 'Payload tx Solana inválido retornado por deBridge', {
          tx
        });
      }

      const serialized = serializedRaw.startsWith('0x')
        ? Buffer.from(serializedRaw.slice(2), 'hex').toString('base64')
        : serializedRaw;

      return {
        chain: 'solana',
        transactionBase64: serialized,
        raw: tx
      };
    }

    throw new OperatorError('DEBRIDGE_CHAIN_UNSUPPORTED', 'Chain origem não suportada para execução bridge', {
      fromChain: preflight.fromChain
    });
  }

  async trackBridge({ orderId, sourceTxHash }) {
    if (!orderId) {
      return {
        available: false,
        reason: 'orderId ausente na resposta deBridge'
      };
    }

    const attempts = Math.max(1, this.trackPollAttempts);
    const snapshots = [];
    let latest = null;

    for (let i = 0; i < attempts; i += 1) {
      const candidates = [
        async () => this.get(`/dln/order/${orderId}/status`),
        async () => this.get('/dln/order/status', { orderId }),
        async () => this.get(`/dln/orders/${orderId}`)
      ];

      for (const candidate of candidates) {
        try {
          const response = await candidate();
          const status = parseBridgeStatus(response);
          latest = {
            at: new Date().toISOString(),
            status,
            response
          };
          snapshots.push(latest);

          if (status && ['fulfilled', 'executed', 'claimed', 'completed', 'sent'].includes(status)) {
            return {
              available: true,
              sourceTxHash,
              orderId,
              completed: true,
              latest,
              snapshots
            };
          }

          break;
        } catch {
          // next candidate endpoint
        }
      }

      if (i < attempts - 1) {
        await sleep(this.trackPollIntervalMs);
      }
    }

    return {
      available: true,
      sourceTxHash,
      orderId,
      completed: false,
      latest,
      snapshots
    };
  }

  async executeBridge(intent, context = {}) {
    try {
      const preflight = await this.preflightBridge(intent);
      const sourceTx = this.parseSourceTransaction(preflight);

      let execution;

      if (sourceTx.chain === 'base' || sourceTx.chain === 'arbitrum') {
        const evm = sourceTx.chain === 'base' ? this.base : this.arbitrum;
        evm.ensureWallet();

        const preflightTx = await evm.preflightContractCall({
          to: sourceTx.to,
          data: sourceTx.data,
          valueWei: sourceTx.valueWei,
          gasLimit: sourceTx.gasLimit,
          maxFeePerGasWei: sourceTx.maxFeePerGasWei,
          maxPriorityFeePerGasWei: sourceTx.maxPriorityFeePerGasWei
        });

        const sentTx = await evm.sendContractCall({
          to: sourceTx.to,
          data: sourceTx.data,
          valueWei: sourceTx.valueWei,
          gasLimit: sourceTx.gasLimit,
          maxFeePerGasWei: sourceTx.maxFeePerGasWei,
          maxPriorityFeePerGasWei: sourceTx.maxPriorityFeePerGasWei
        });

        execution = {
          chain: sourceTx.chain,
          preflight: preflightTx,
          sent: sentTx,
          sourceTxHash: sentTx.txHash
        };
      } else if (sourceTx.chain === 'solana') {
        this.solana.ensureWallet();

        const preflightTx = await this.solana.preflightSerializedTransaction({
          transactionBase64: sourceTx.transactionBase64,
          label: 'debridge.bridge'
        });

        const sentTx = await this.solana.sendSerializedTransaction({
          transactionBase64: sourceTx.transactionBase64,
          label: 'debridge.bridge',
          skipPreflight: false,
          maxRetries: 3
        });

        execution = {
          chain: 'solana',
          preflight: preflightTx,
          sent: sentTx,
          sourceTxHash: sentTx.txHash
        };
      } else {
        throw new OperatorError('DEBRIDGE_CHAIN_UNSUPPORTED', 'Chain origem não suportada para execução bridge');
      }

      const tracking = await this.trackBridge({
        orderId: preflight.orderId,
        sourceTxHash: execution.sourceTxHash
      });

      return {
        chain: 'debridge',
        connector: 'debridge',
        action: 'bridge',
        idempotencyKey: context.idempotencyKey ?? null,
        preflight,
        execution,
        tracking
      };
    } catch (error) {
      if (error instanceof OperatorError) throw error;
      throw new OperatorError('DEBRIDGE_EXECUTION_FAILED', 'Falha na execução live da bridge deBridge', {
        message: error.message
      });
    }
  }
}
