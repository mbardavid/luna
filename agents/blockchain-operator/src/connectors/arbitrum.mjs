import {
  createPublicClient,
  createWalletClient,
  formatEther,
  formatUnits,
  http,
  isAddress,
  parseEther,
  stringify,
  zeroAddress
} from 'viem';
import { privateKeyToAccount } from 'viem/accounts';
import { arbitrum } from 'viem/chains';
import { OperatorError } from '../utils/errors.mjs';
import { decimalToAtomic } from './token-registry.mjs';

const ERC20_ABI = [
  {
    type: 'function',
    name: 'balanceOf',
    stateMutability: 'view',
    inputs: [{ name: 'account', type: 'address' }],
    outputs: [{ name: 'balance', type: 'uint256' }]
  },
  {
    type: 'function',
    name: 'transfer',
    stateMutability: 'nonpayable',
    inputs: [
      { name: 'recipient', type: 'address' },
      { name: 'amount', type: 'uint256' }
    ],
    outputs: [{ name: 'success', type: 'bool' }]
  }
];

function normalizePrivateKey(value) {
  if (!value) return null;
  return value.startsWith('0x') ? value : `0x${value}`;
}

function parseBigIntLike(value, field) {
  if (value == null) return null;

  try {
    const normalized = typeof value === 'string' ? value.trim() : value;
    return BigInt(normalized);
  } catch {
    throw new OperatorError('ARBITRUM_NUMBER_INVALID', `${field} inválido`, {
      field,
      value
    });
  }
}

function parseCallValue({ value, valueWei }) {
  if (valueWei != null) {
    const wei = parseBigIntLike(valueWei, 'valueWei');
    if (wei < 0n) {
      throw new OperatorError('ARBITRUM_VALUE_INVALID', 'valueWei não pode ser negativo', { valueWei });
    }
    return wei;
  }

  const normalized = value == null ? '0' : String(value);
  try {
    return parseEther(normalized);
  } catch {
    throw new OperatorError('ARBITRUM_VALUE_INVALID', 'value inválido para ETH', { value });
  }
}

function parseOptionalGas(gasLimit) {
  if (gasLimit == null) return undefined;

  const gas = parseBigIntLike(gasLimit, 'gasLimit');
  if (gas <= 0n) {
    throw new OperatorError('ARBITRUM_GAS_INVALID', 'gasLimit deve ser > 0', { gasLimit });
  }

  return gas;
}

function parseOptionalFee(value, field) {
  if (value == null) return undefined;
  const parsed = parseBigIntLike(value, field);
  if (parsed < 0n) {
    throw new OperatorError('ARBITRUM_FEE_INVALID', `${field} não pode ser negativo`, {
      field,
      value
    });
  }
  return parsed;
}

export class ArbitrumConnector {
  constructor({ rpcUrl, privateKey } = {}) {
    this.rpcUrl = rpcUrl ?? process.env.ARBITRUM_RPC_URL ?? 'https://arb1.arbitrum.io/rpc';
    this.privateKey = normalizePrivateKey(
      privateKey ?? process.env.ARBITRUM_PRIVATE_KEY ?? process.env.BASE_PRIVATE_KEY ?? ''
    );

    this.publicClient = createPublicClient({
      chain: arbitrum,
      transport: http(this.rpcUrl)
    });

    this.account = this.privateKey ? privateKeyToAccount(this.privateKey) : null;
    this.walletClient = this.account
      ? createWalletClient({
          account: this.account,
          chain: arbitrum,
          transport: http(this.rpcUrl)
        })
      : null;
  }

  getAddress() {
    return this.account?.address ?? null;
  }

  ensureWallet() {
    if (!this.walletClient || !this.account) {
      throw new OperatorError(
        'ARBITRUM_KEY_MISSING',
        'ARBITRUM_PRIVATE_KEY (ou fallback BASE_PRIVATE_KEY) não configurada. Impossível executar envio real.'
      );
    }
  }

  async preflightContractCall({ to, data, value, valueWei, gasLimit, maxFeePerGasWei, maxPriorityFeePerGasWei }) {
    if (!isAddress(to)) {
      throw new OperatorError('ARBITRUM_CONTRACT_INVALID', 'Contrato inválido', { to });
    }
    if (!/^0x[a-fA-F0-9]*$/.test(data)) {
      throw new OperatorError('ARBITRUM_CALLDATA_INVALID', 'Calldata inválida', { data });
    }

    const valueWeiParsed = parseCallValue({ value, valueWei });
    const gasLimitParsed = parseOptionalGas(gasLimit);
    const maxFeePerGasParsed = parseOptionalFee(maxFeePerGasWei, 'maxFeePerGasWei');
    const maxPriorityFeePerGasParsed = parseOptionalFee(
      maxPriorityFeePerGasWei,
      'maxPriorityFeePerGasWei'
    );

    if (!this.account) {
      return {
        chain: 'arbitrum',
        to,
        valueWei: valueWeiParsed.toString(),
        walletReady: false,
        note: 'ARBITRUM_PRIVATE_KEY ausente: dry-run parcial (sem validação de saldo/fee).'
      };
    }

    const from = this.account.address;

    const [balanceWei, estimatedGas, fees] = await Promise.all([
      this.publicClient.getBalance({ address: from }),
      this.publicClient.estimateGas({
        account: from,
        to,
        data,
        value: valueWeiParsed
      }),
      this.publicClient.estimateFeesPerGas()
    ]);

    const gasEstimate = gasLimitParsed ?? estimatedGas;
    const maxFeePerGas = maxFeePerGasParsed ?? fees.maxFeePerGas ?? fees.gasPrice ?? 0n;
    const maxPriorityFeePerGas = maxPriorityFeePerGasParsed ?? fees.maxPriorityFeePerGas ?? null;
    const estimatedFeeWei = gasEstimate * maxFeePerGas;

    if (balanceWei < estimatedFeeWei + valueWeiParsed) {
      throw new OperatorError('ARBITRUM_INSUFFICIENT_BALANCE', 'Saldo insuficiente para contract call', {
        balanceEth: formatEther(balanceWei),
        neededEth: formatEther(estimatedFeeWei + valueWeiParsed)
      });
    }

    return {
      chain: 'arbitrum',
      from,
      to,
      valueWei: valueWeiParsed.toString(),
      valueEth: formatEther(valueWeiParsed),
      gasEstimate: gasEstimate.toString(),
      maxFeePerGasWei: maxFeePerGas.toString(),
      maxFeePerGasGwei: formatUnits(maxFeePerGas, 9),
      maxPriorityFeePerGasWei: maxPriorityFeePerGas?.toString() ?? null,
      estimatedFeeEth: formatEther(estimatedFeeWei),
      balanceEth: formatEther(balanceWei)
    };
  }

  async sendContractCall({ to, data, value, valueWei, gasLimit, maxFeePerGasWei, maxPriorityFeePerGasWei }) {
    const preflight = await this.preflightContractCall({
      to,
      data,
      value,
      valueWei,
      gasLimit,
      maxFeePerGasWei,
      maxPriorityFeePerGasWei
    });

    const hash = await this.walletClient.sendTransaction({
      account: this.account,
      to,
      data,
      value: parseCallValue({ value, valueWei }),
      gas: parseOptionalGas(gasLimit),
      maxFeePerGas: parseOptionalFee(maxFeePerGasWei, 'maxFeePerGasWei'),
      maxPriorityFeePerGas: parseOptionalFee(maxPriorityFeePerGasWei, 'maxPriorityFeePerGasWei'),
      chain: arbitrum
    });

    const receipt = await this.publicClient.waitForTransactionReceipt({ hash });

    return {
      ...preflight,
      txHash: hash,
      receipt: JSON.parse(stringify(receipt))
    };
  }

  async preflightErc20Transfer({ tokenAddress, amount, decimals = 6, symbol = 'TOKEN', to }) {
    if (!isAddress(tokenAddress) || tokenAddress === zeroAddress) {
      throw new OperatorError('ARBITRUM_TOKEN_INVALID', 'tokenAddress inválido para ERC20 transfer', {
        tokenAddress
      });
    }

    if (!isAddress(to) || to === zeroAddress) {
      throw new OperatorError('ARBITRUM_ADDRESS_INVALID', 'Endereço destino inválido para ERC20 transfer', {
        to
      });
    }

    const amountAtomic = decimalToAtomic(amount, decimals, {
      field: 'amount',
      errorCode: 'ARBITRUM_AMOUNT_INVALID'
    });

    if (!this.account) {
      return {
        chain: 'arbitrum',
        action: 'erc20.transfer',
        tokenAddress,
        tokenSymbol: symbol,
        to,
        amount: String(amount),
        amountAtomic,
        walletReady: false,
        note: 'ARBITRUM_PRIVATE_KEY ausente: dry-run parcial (sem validação de saldo/fee).'
      };
    }

    const from = this.account.address;

    const [tokenBalance, gasEstimate, fees, ethBalance] = await Promise.all([
      this.publicClient.readContract({
        address: tokenAddress,
        abi: ERC20_ABI,
        functionName: 'balanceOf',
        args: [from]
      }),
      this.publicClient.estimateContractGas({
        account: from,
        address: tokenAddress,
        abi: ERC20_ABI,
        functionName: 'transfer',
        args: [to, BigInt(amountAtomic)]
      }),
      this.publicClient.estimateFeesPerGas(),
      this.publicClient.getBalance({ address: from })
    ]);

    const maxFeePerGas = fees.maxFeePerGas ?? fees.gasPrice ?? 0n;
    const estimatedFeeWei = gasEstimate * maxFeePerGas;

    if (tokenBalance < BigInt(amountAtomic)) {
      throw new OperatorError('ARBITRUM_TOKEN_BALANCE_INSUFFICIENT', 'Saldo ERC20 insuficiente para transferência', {
        tokenSymbol: symbol,
        tokenAddress,
        amount,
        available: formatUnits(tokenBalance, decimals)
      });
    }

    if (ethBalance < estimatedFeeWei) {
      throw new OperatorError('ARBITRUM_GAS_BALANCE_INSUFFICIENT', 'Saldo ETH insuficiente para gas da transferência', {
        balanceEth: formatEther(ethBalance),
        estimatedFeeEth: formatEther(estimatedFeeWei)
      });
    }

    return {
      chain: 'arbitrum',
      action: 'erc20.transfer',
      from,
      tokenAddress,
      tokenSymbol: symbol,
      decimals,
      to,
      amount: String(amount),
      amountAtomic,
      tokenBalance: formatUnits(tokenBalance, decimals),
      gasEstimate: gasEstimate.toString(),
      maxFeePerGasWei: maxFeePerGas.toString(),
      estimatedFeeEth: formatEther(estimatedFeeWei),
      gasBalanceEth: formatEther(ethBalance),
      walletReady: true
    };
  }

  async sendErc20Transfer({ tokenAddress, amount, decimals = 6, symbol = 'TOKEN', to }) {
    this.ensureWallet();

    const preflight = await this.preflightErc20Transfer({ tokenAddress, amount, decimals, symbol, to });

    const txHash = await this.walletClient.writeContract({
      account: this.account,
      chain: arbitrum,
      address: tokenAddress,
      abi: ERC20_ABI,
      functionName: 'transfer',
      args: [to, BigInt(preflight.amountAtomic)]
    });

    const receipt = await this.publicClient.waitForTransactionReceipt({ hash: txHash });

    return {
      ...preflight,
      txHash,
      receipt: JSON.parse(stringify(receipt))
    };
  }
}
