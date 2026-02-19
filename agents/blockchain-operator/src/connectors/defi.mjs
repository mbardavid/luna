import { encodeFunctionData, isAddress } from 'viem';
import { OperatorError } from '../utils/errors.mjs';
import { BaseConnector } from './base.mjs';
import { decimalToAtomic, resolveBaseToken } from './token-registry.mjs';

const ERC20_ABI = [
  {
    type: 'function',
    name: 'balanceOf',
    stateMutability: 'view',
    inputs: [{ name: 'account', type: 'address' }],
    outputs: [{ name: '', type: 'uint256' }]
  },
  {
    type: 'function',
    name: 'allowance',
    stateMutability: 'view',
    inputs: [
      { name: 'owner', type: 'address' },
      { name: 'spender', type: 'address' }
    ],
    outputs: [{ name: '', type: 'uint256' }]
  },
  {
    type: 'function',
    name: 'approve',
    stateMutability: 'nonpayable',
    inputs: [
      { name: 'spender', type: 'address' },
      { name: 'amount', type: 'uint256' }
    ],
    outputs: [{ name: '', type: 'bool' }]
  }
];

const AAVE_POOL_ABI = [
  {
    type: 'function',
    name: 'supply',
    stateMutability: 'nonpayable',
    inputs: [
      { name: 'asset', type: 'address' },
      { name: 'amount', type: 'uint256' },
      { name: 'onBehalfOf', type: 'address' },
      { name: 'referralCode', type: 'uint16' }
    ],
    outputs: []
  },
  {
    type: 'function',
    name: 'withdraw',
    stateMutability: 'nonpayable',
    inputs: [
      { name: 'asset', type: 'address' },
      { name: 'amount', type: 'uint256' },
      { name: 'to', type: 'address' }
    ],
    outputs: [{ name: '', type: 'uint256' }]
  }
];

const DEFAULT_AAVE_BASE_POOL = '0xA238Dd80C259a72e81d7e4664a9801593F98d1c5';

function normalizeProtocol(value) {
  return String(value ?? '').trim().toLowerCase();
}

function normalizeTarget(value) {
  return String(value ?? '').trim();
}

function ensureAddress(value, field) {
  if (!isAddress(value)) {
    throw new OperatorError('DEFI_ADDRESS_INVALID', `${field} inválido`, {
      field,
      value
    });
  }

  return value;
}

function ensureAtomicAmount(intent) {
  const token = resolveBaseToken(intent.asset, {
    field: 'asset',
    errorCode: 'DEFI_ASSET_UNSUPPORTED'
  });

  if (token.native) {
    throw new OperatorError(
      'DEFI_ASSET_UNSUPPORTED',
      'Deposit/withdraw DeFi atual exige token ERC20 (asset nativo não suportado diretamente).',
      {
        asset: intent.asset,
        chain: intent.chain
      }
    );
  }

  const amountAtomic = decimalToAtomic(intent.amount, token.decimals, {
    field: 'amount',
    errorCode: 'DEFI_AMOUNT_INVALID'
  });

  return {
    token,
    amountAtomic
  };
}

class DefiAdapter {
  supports(_intent) {
    return false;
  }

  async preflightDeposit(_intent) {
    throw new OperatorError('DEFI_ADAPTER_NOT_IMPLEMENTED', 'preflightDeposit não implementado');
  }

  async executeDeposit(_intent, _context) {
    throw new OperatorError('DEFI_ADAPTER_NOT_IMPLEMENTED', 'executeDeposit não implementado');
  }

  async preflightWithdraw(_intent) {
    throw new OperatorError('DEFI_ADAPTER_NOT_IMPLEMENTED', 'preflightWithdraw não implementado');
  }

  async executeWithdraw(_intent, _context) {
    throw new OperatorError('DEFI_ADAPTER_NOT_IMPLEMENTED', 'executeWithdraw não implementado');
  }
}

class AaveV3BaseAdapter extends DefiAdapter {
  constructor({ baseConnector, poolAddress, referralCode = 0 } = {}) {
    super();
    this.base = baseConnector ?? new BaseConnector({});
    this.poolAddress = ensureAddress(
      poolAddress ?? process.env.AAVE_V3_BASE_POOL_ADDRESS ?? DEFAULT_AAVE_BASE_POOL,
      'AAVE_V3_BASE_POOL_ADDRESS'
    );
    this.referralCode = Number(referralCode);
  }

  supports(intent) {
    if (intent.chain !== 'base') return false;

    const protocol = normalizeProtocol(intent.protocol);
    return ['aave-v3', 'aave'].includes(protocol);
  }

  resolveOwner(intent) {
    const signer = this.base.getAddress();

    if (intent.action === 'defi_deposit') {
      if (intent.recipient) {
        return ensureAddress(intent.recipient, 'recipient');
      }

      return signer ?? null;
    }

    return ensureAddress(intent.recipient, 'recipient');
  }

  async readTokenState({ tokenAddress, owner }) {
    if (!owner) {
      return {
        allowance: null,
        balance: null
      };
    }

    const [allowance, balance] = await Promise.all([
      this.base.publicClient.readContract({
        address: tokenAddress,
        abi: ERC20_ABI,
        functionName: 'allowance',
        args: [owner, this.poolAddress]
      }),
      this.base.publicClient.readContract({
        address: tokenAddress,
        abi: ERC20_ABI,
        functionName: 'balanceOf',
        args: [owner]
      })
    ]);

    return {
      allowance: BigInt(allowance),
      balance: BigInt(balance)
    };
  }

  buildDepositTransactions({ token, amountAtomic, owner }) {
    const amountWei = BigInt(amountAtomic);

    const approveData = encodeFunctionData({
      abi: ERC20_ABI,
      functionName: 'approve',
      args: [this.poolAddress, amountWei]
    });

    const supplyData = encodeFunctionData({
      abi: AAVE_POOL_ABI,
      functionName: 'supply',
      args: [token.address, amountWei, owner, this.referralCode]
    });

    return [
      {
        id: 'approve',
        to: token.address,
        data: approveData,
        valueWei: '0'
      },
      {
        id: 'supply',
        to: this.poolAddress,
        data: supplyData,
        valueWei: '0'
      }
    ];
  }

  buildWithdrawTransaction({ token, amountAtomic, recipient }) {
    const amountWei = BigInt(amountAtomic);

    const withdrawData = encodeFunctionData({
      abi: AAVE_POOL_ABI,
      functionName: 'withdraw',
      args: [token.address, amountWei, recipient]
    });

    return {
      id: 'withdraw',
      to: this.poolAddress,
      data: withdrawData,
      valueWei: '0'
    };
  }

  async preflightDeposit(intent) {
    const target = normalizeTarget(intent.target);
    const owner = this.resolveOwner(intent);
    const signerAddress = this.base.getAddress();
    const { token, amountAtomic } = ensureAtomicAmount(intent);

    const minSharesOutAtomic =
      intent.minSharesOut == null
        ? null
        : decimalToAtomic(intent.minSharesOut, token.decimals, {
            field: 'minSharesOut',
            errorCode: 'DEFI_MIN_SHARES_INVALID'
          });

    if (minSharesOutAtomic != null && BigInt(minSharesOutAtomic) > BigInt(amountAtomic)) {
      throw new OperatorError(
        'DEFI_MIN_SHARES_INVALID',
        'minSharesOut não pode ser maior que amount para o adapter Aave v3 atual.',
        {
          amountAtomic,
          minSharesOutAtomic
        }
      );
    }

    const txOwner = owner ?? signerAddress ?? '0x000000000000000000000000000000000000dEaD';
    const txPlan = this.buildDepositTransactions({ token, amountAtomic, owner: txOwner });

    if (!signerAddress) {
      return {
        chain: 'base',
        connector: 'defi',
        protocol: 'aave-v3',
        action: 'defi_deposit',
        target,
        poolAddress: this.poolAddress,
        asset: token.symbol,
        amount: String(intent.amount),
        amountAtomic,
        minSharesOutAtomic,
        recipient: owner,
        walletReady: false,
        txPlan,
        note: 'BASE_PRIVATE_KEY ausente: dry-run sem validação de allowance/balance.'
      };
    }

    const ownerAddress = owner ?? signerAddress;
    const tokenState = await this.readTokenState({
      tokenAddress: token.address,
      owner: ownerAddress
    });

    const amountWei = BigInt(amountAtomic);

    if (tokenState.balance != null && tokenState.balance < amountWei) {
      throw new OperatorError('DEFI_BALANCE_INSUFFICIENT', 'Saldo insuficiente para deposit no protocolo', {
        protocol: 'aave-v3',
        asset: token.symbol,
        balance: tokenState.balance.toString(),
        required: amountAtomic
      });
    }

    const requiredApprovalWei =
      tokenState.allowance == null ? null : tokenState.allowance >= amountWei ? 0n : amountWei - tokenState.allowance;

    const [approvePreflight, supplyPreflight] = await Promise.all([
      this.base.preflightContractCall({
        to: txPlan[0].to,
        data: txPlan[0].data,
        valueWei: txPlan[0].valueWei
      }),
      this.base.preflightContractCall({
        to: txPlan[1].to,
        data: txPlan[1].data,
        valueWei: txPlan[1].valueWei
      })
    ]);

    return {
      chain: 'base',
      connector: 'defi',
      protocol: 'aave-v3',
      action: 'defi_deposit',
      target,
      poolAddress: this.poolAddress,
      asset: token.symbol,
      tokenAddress: token.address,
      amount: String(intent.amount),
      amountAtomic,
      minSharesOutAtomic,
      recipient: ownerAddress,
      walletReady: true,
      allowanceAtomic: tokenState.allowance?.toString() ?? null,
      balanceAtomic: tokenState.balance?.toString() ?? null,
      requiredApprovalAtomic: requiredApprovalWei?.toString() ?? null,
      txPlan,
      preflightSteps: {
        approve: approvePreflight,
        supply: supplyPreflight
      }
    };
  }

  async executeDeposit(intent, context = {}) {
    this.base.ensureWallet();

    const preflight = await this.preflightDeposit(intent);

    const shouldApprove =
      preflight.requiredApprovalAtomic == null || BigInt(preflight.requiredApprovalAtomic) > 0n;

    const executions = [];
    for (const step of preflight.txPlan) {
      if (step.id === 'approve' && !shouldApprove) continue;

      const exec = await this.base.sendContractCall({
        to: step.to,
        data: step.data,
        valueWei: step.valueWei
      });

      executions.push({
        id: step.id,
        txHash: exec.txHash,
        receipt: exec.receipt
      });
    }

    return {
      chain: 'base',
      connector: 'defi',
      protocol: 'aave-v3',
      action: 'defi_deposit',
      idempotencyKey: context.idempotencyKey ?? null,
      preflight,
      executions
    };
  }

  async preflightWithdraw(intent) {
    const target = normalizeTarget(intent.target);
    const recipient = this.resolveOwner(intent);
    const { token, amountAtomic } = ensureAtomicAmount(intent);

    const tx = this.buildWithdrawTransaction({ token, amountAtomic, recipient });

    if (!this.base.getAddress()) {
      return {
        chain: 'base',
        connector: 'defi',
        protocol: 'aave-v3',
        action: 'defi_withdraw',
        target,
        poolAddress: this.poolAddress,
        asset: token.symbol,
        amount: String(intent.amount),
        amountType: intent.amountType ?? 'asset',
        recipient,
        walletReady: false,
        txPlan: [tx],
        note: 'BASE_PRIVATE_KEY ausente: dry-run sem validação de posição no protocolo.'
      };
    }

    const preflight = await this.base.preflightContractCall({
      to: tx.to,
      data: tx.data,
      valueWei: tx.valueWei
    });

    return {
      chain: 'base',
      connector: 'defi',
      protocol: 'aave-v3',
      action: 'defi_withdraw',
      target,
      poolAddress: this.poolAddress,
      asset: token.symbol,
      tokenAddress: token.address,
      amount: String(intent.amount),
      amountAtomic,
      amountType: intent.amountType ?? 'asset',
      recipient,
      walletReady: true,
      txPlan: [tx],
      preflightSteps: {
        withdraw: preflight
      }
    };
  }

  async executeWithdraw(intent, context = {}) {
    this.base.ensureWallet();

    const preflight = await this.preflightWithdraw(intent);
    const tx = preflight.txPlan[0];

    const execution = await this.base.sendContractCall({
      to: tx.to,
      data: tx.data,
      valueWei: tx.valueWei
    });

    return {
      chain: 'base',
      connector: 'defi',
      protocol: 'aave-v3',
      action: 'defi_withdraw',
      idempotencyKey: context.idempotencyKey ?? null,
      preflight,
      execution: {
        id: tx.id,
        txHash: execution.txHash,
        receipt: execution.receipt
      }
    };
  }
}

export class DefiConnector {
  constructor({ adapters } = {}) {
    this.adapters = adapters ?? [new AaveV3BaseAdapter({})];
  }

  resolveAdapter(intent) {
    const protocol = normalizeProtocol(intent.protocol);

    const adapter = this.adapters.find((candidate) => candidate.supports(intent));
    if (!adapter) {
      throw new OperatorError(
        'DEFI_PROTOCOL_UNSUPPORTED',
        `Protocolo DeFi não suportado para ${intent.action}: ${protocol}`,
        {
          protocol,
          chain: intent.chain,
          supported: ['aave-v3(base)']
        }
      );
    }

    return adapter;
  }

  async preflightDeposit(intent) {
    try {
      const adapter = this.resolveAdapter(intent);
      return adapter.preflightDeposit(intent);
    } catch (error) {
      if (error instanceof OperatorError) throw error;
      throw new OperatorError('DEFI_DEPOSIT_PREFLIGHT_FAILED', 'Falha no preflight de deposit DeFi', {
        message: error.message
      });
    }
  }

  async executeDeposit(intent, context = {}) {
    try {
      const adapter = this.resolveAdapter(intent);
      return adapter.executeDeposit(intent, context);
    } catch (error) {
      if (error instanceof OperatorError) throw error;
      throw new OperatorError('DEFI_DEPOSIT_EXECUTION_FAILED', 'Falha na execução de deposit DeFi', {
        message: error.message
      });
    }
  }

  async preflightWithdraw(intent) {
    try {
      const adapter = this.resolveAdapter(intent);
      return adapter.preflightWithdraw(intent);
    } catch (error) {
      if (error instanceof OperatorError) throw error;
      throw new OperatorError('DEFI_WITHDRAW_PREFLIGHT_FAILED', 'Falha no preflight de withdraw DeFi', {
        message: error.message
      });
    }
  }

  async executeWithdraw(intent, context = {}) {
    try {
      const adapter = this.resolveAdapter(intent);
      return adapter.executeWithdraw(intent, context);
    } catch (error) {
      if (error instanceof OperatorError) throw error;
      throw new OperatorError('DEFI_WITHDRAW_EXECUTION_FAILED', 'Falha na execução de withdraw DeFi', {
        message: error.message
      });
    }
  }
}
