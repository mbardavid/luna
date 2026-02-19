import { isAddress, zeroAddress } from 'viem';
import { ArbitrumConnector } from './arbitrum.mjs';
import { HyperliquidConnector } from './hyperliquid.mjs';
import { resolveArbitrumToken } from './token-registry.mjs';
import { OperatorError } from '../utils/errors.mjs';

const DEFAULT_BRIDGE2_ADDRESS = '0x2df1c51e09aecf9cacb7bc98cb1742757f163df7';
const DEFAULT_MIN_DEPOSIT_USDC = 5;

function normalizeAmount(value, field = 'amount') {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) {
    throw new OperatorError('HL_BRIDGE_AMOUNT_INVALID', `${field} deve ser > 0`, {
      field,
      value
    });
  }
  return String(n);
}

function ensureUsdcAsset(asset) {
  if (String(asset ?? '').toUpperCase() !== 'USDC') {
    throw new OperatorError('HL_BRIDGE_ASSET_UNSUPPORTED', 'Hyperliquid native bridge suporta apenas USDC', {
      asset
    });
  }
}

function normalizeRecipient(recipient) {
  if (!recipient || !isAddress(recipient) || recipient === zeroAddress) {
    throw new OperatorError('HL_BRIDGE_RECIPIENT_INVALID', 'recipient inválido para Arbitrum/EVM', {
      recipient
    });
  }

  return recipient;
}

export class HyperliquidBridgeConnector {
  constructor({ arbitrum, hyperliquid, bridgeAddress, minDepositUsdc } = {}) {
    this.arbitrum = arbitrum ?? new ArbitrumConnector({});
    this.hyperliquid = hyperliquid ?? new HyperliquidConnector({});

    this.bridgeAddress = bridgeAddress ?? process.env.HYPERLIQUID_BRIDGE2_ADDRESS ?? DEFAULT_BRIDGE2_ADDRESS;
    this.minDepositUsdc = Number(
      minDepositUsdc ?? process.env.HYPERLIQUID_BRIDGE_MIN_DEPOSIT_USDC ?? DEFAULT_MIN_DEPOSIT_USDC
    );

    if (!isAddress(this.bridgeAddress) || this.bridgeAddress === zeroAddress) {
      throw new OperatorError('HL_BRIDGE_CONFIG_INVALID', 'HYPERLIQUID_BRIDGE2_ADDRESS inválido', {
        bridgeAddress: this.bridgeAddress
      });
    }

    if (!Number.isFinite(this.minDepositUsdc) || this.minDepositUsdc <= 0) {
      throw new OperatorError('HL_BRIDGE_CONFIG_INVALID', 'HYPERLIQUID_BRIDGE_MIN_DEPOSIT_USDC inválido', {
        minDepositUsdc: this.minDepositUsdc
      });
    }
  }

  ensureRoute(intent, expectedFrom, expectedTo) {
    if (intent.fromChain !== expectedFrom || intent.toChain !== expectedTo) {
      throw new OperatorError('HL_BRIDGE_ROUTE_INVALID', 'Rota inválida para operação native bridge Hyperliquid', {
        expectedFrom,
        expectedTo,
        fromChain: intent.fromChain,
        toChain: intent.toChain
      });
    }
  }

  buildAccountMatchCheck() {
    const fromArbitrum = this.arbitrum.getAddress();
    const hlAccount = this.hyperliquid.getAccountAddress();

    if (!fromArbitrum || !hlAccount) {
      return {
        accountMatch: null,
        fromArbitrum: fromArbitrum ?? null,
        hyperliquidAccount: hlAccount ?? null,
        warning:
          'Não foi possível validar match de endereço (ARBITRUM_PRIVATE_KEY e/ou HYPERLIQUID_ACCOUNT_ADDRESS ausentes).'
      };
    }

    const accountMatch = fromArbitrum.toLowerCase() === hlAccount.toLowerCase();
    if (!accountMatch) {
      throw new OperatorError(
        'HL_BRIDGE_ACCOUNT_MISMATCH',
        'Bridge2 deposita no endereço remetente. Configure ARBITRUM_PRIVATE_KEY para o mesmo endereço de HYPERLIQUID_ACCOUNT_ADDRESS.',
        {
          fromArbitrum,
          hyperliquidAccount: hlAccount,
          bridgeAddress: this.bridgeAddress
        }
      );
    }

    return {
      accountMatch,
      fromArbitrum,
      hyperliquidAccount: hlAccount,
      warning: null
    };
  }

  async preflightDeposit(intent) {
    this.ensureRoute(intent, 'arbitrum', 'hyperliquid');
    ensureUsdcAsset(intent.asset);

    const amount = normalizeAmount(intent.amount, 'amount');
    if (Number(amount) < this.minDepositUsdc) {
      throw new OperatorError(
        'HL_BRIDGE_MIN_DEPOSIT',
        `Bridge2 exige depósito mínimo de ${this.minDepositUsdc} USDC no mainnet Hyperliquid.`,
        {
          requestedAmount: amount,
          minDepositUsdc: this.minDepositUsdc,
          bridgeAddress: this.bridgeAddress
        }
      );
    }

    const usdc = resolveArbitrumToken('USDC', {
      field: 'asset',
      errorCode: 'HL_BRIDGE_ASSET_UNSUPPORTED'
    });

    const accountCheck = this.buildAccountMatchCheck();

    const transfer = await this.arbitrum.preflightErc20Transfer({
      tokenAddress: usdc.address,
      symbol: usdc.symbol,
      decimals: usdc.decimals,
      to: this.bridgeAddress,
      amount
    });

    return {
      connector: 'hyperliquid_bridge',
      action: 'hl_bridge_deposit',
      route: 'arbitrum->hyperliquid',
      fromChain: 'arbitrum',
      toChain: 'hyperliquid',
      asset: 'USDC',
      amount,
      bridgeAddress: this.bridgeAddress,
      minDepositUsdc: this.minDepositUsdc,
      docs: 'https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/bridge2',
      accountCheck,
      transfer
    };
  }

  async executeDeposit(intent) {
    const preflight = await this.preflightDeposit(intent);

    const execution = await this.arbitrum.sendErc20Transfer({
      tokenAddress: preflight.transfer.tokenAddress,
      symbol: preflight.transfer.tokenSymbol,
      decimals: preflight.transfer.decimals,
      to: this.bridgeAddress,
      amount: preflight.amount
    });

    return {
      connector: 'hyperliquid_bridge',
      action: 'hl_bridge_deposit',
      preflight,
      execution,
      note: 'Depósito Bridge2 credita o endereço remetente no Hyperliquid após confirmação Arbitrum.'
    };
  }

  async preflightWithdraw(intent) {
    this.ensureRoute(intent, 'hyperliquid', 'arbitrum');
    ensureUsdcAsset(intent.asset);

    const amount = normalizeAmount(intent.amount, 'amount');
    const recipient = normalizeRecipient(intent.recipient);

    const hyperliquid = await this.hyperliquid.preflightBridgeWithdraw({
      ...intent,
      amount,
      recipient
    });

    return {
      connector: 'hyperliquid_bridge',
      action: 'hl_bridge_withdraw',
      route: 'hyperliquid->arbitrum',
      fromChain: 'hyperliquid',
      toChain: 'arbitrum',
      asset: 'USDC',
      amount,
      recipient,
      bridgeAddress: this.bridgeAddress,
      docs: 'https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/bridge2',
      hyperliquid
    };
  }

  async executeWithdraw(intent) {
    const preflight = await this.preflightWithdraw(intent);

    const execution = await this.hyperliquid.withdrawFromBridge({
      ...intent,
      amount: preflight.amount,
      recipient: preflight.recipient
    });

    return {
      connector: 'hyperliquid_bridge',
      action: 'hl_bridge_withdraw',
      preflight,
      execution,
      note: 'Withdraw3 em Hyperliquid dispara saque para wallet Arbitrum via validadores.'
    };
  }
}
