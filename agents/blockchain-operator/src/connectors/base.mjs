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
import { base } from 'viem/chains';
import { OperatorError } from '../utils/errors.mjs';

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
    throw new OperatorError('BASE_NUMBER_INVALID', `${field} inválido`, {
      field,
      value
    });
  }
}

function parseCallValue({ value, valueWei }) {
  if (valueWei != null) {
    const wei = parseBigIntLike(valueWei, 'valueWei');
    if (wei < 0n) {
      throw new OperatorError('BASE_VALUE_INVALID', 'valueWei não pode ser negativo', { valueWei });
    }
    return wei;
  }

  const normalized = value == null ? '0' : String(value);
  try {
    return parseEther(normalized);
  } catch {
    throw new OperatorError('BASE_VALUE_INVALID', 'value inválido para ETH', { value });
  }
}

function parseOptionalGas(gasLimit) {
  if (gasLimit == null) return undefined;

  const gas = parseBigIntLike(gasLimit, 'gasLimit');
  if (gas <= 0n) {
    throw new OperatorError('BASE_GAS_INVALID', 'gasLimit deve ser > 0', { gasLimit });
  }

  return gas;
}

function parseOptionalFee(value, field) {
  if (value == null) return undefined;
  const parsed = parseBigIntLike(value, field);
  if (parsed < 0n) {
    throw new OperatorError('BASE_FEE_INVALID', `${field} não pode ser negativo`, {
      field,
      value
    });
  }
  return parsed;
}

export class BaseConnector {
  constructor({ rpcUrl, privateKey }) {
    this.rpcUrl = rpcUrl ?? process.env.BASE_RPC_URL ?? 'https://mainnet.base.org';
    this.privateKey = normalizePrivateKey(privateKey ?? process.env.BASE_PRIVATE_KEY ?? '');

    this.publicClient = createPublicClient({
      chain: base,
      transport: http(this.rpcUrl)
    });

    this.account = this.privateKey ? privateKeyToAccount(this.privateKey) : null;
    this.walletClient = this.account
      ? createWalletClient({
          account: this.account,
          chain: base,
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
        'BASE_KEY_MISSING',
        'BASE_PRIVATE_KEY não configurada. Impossível executar envio real.'
      );
    }
  }

  async preflightNativeTransfer({ to, amount }) {
    if (!isAddress(to) || to === zeroAddress) {
      throw new OperatorError('BASE_ADDRESS_INVALID', 'Endereço destino inválido para Base', { to });
    }

    if (!this.account) {
      return {
        chain: 'base',
        to,
        valueEth: amount,
        walletReady: false,
        note: 'BASE_PRIVATE_KEY ausente: dry-run parcial (sem validação de saldo/fee).'
      };
    }

    const value = parseEther(amount);
    const from = this.account.address;

    const [balanceWei, gasEstimate, fees] = await Promise.all([
      this.publicClient.getBalance({ address: from }),
      this.publicClient.estimateGas({ account: from, to, value }),
      this.publicClient.estimateFeesPerGas()
    ]);

    const maxFeePerGas = fees.maxFeePerGas ?? fees.gasPrice ?? 0n;
    const estimatedFeeWei = gasEstimate * maxFeePerGas;
    const totalEstimated = value + estimatedFeeWei;

    if (balanceWei < totalEstimated) {
      throw new OperatorError('BASE_INSUFFICIENT_BALANCE', 'Saldo insuficiente para valor + fee estimada', {
        balanceEth: formatEther(balanceWei),
        neededEth: formatEther(totalEstimated)
      });
    }

    return {
      chain: 'base',
      from,
      to,
      valueEth: amount,
      gasEstimate: gasEstimate.toString(),
      maxFeePerGasWei: maxFeePerGas.toString(),
      estimatedFeeEth: formatEther(estimatedFeeWei),
      balanceEth: formatEther(balanceWei)
    };
  }

  async sendNativeTransfer({ to, amount }) {
    const preflight = await this.preflightNativeTransfer({ to, amount });
    const hash = await this.walletClient.sendTransaction({
      account: this.account,
      to,
      value: parseEther(amount),
      chain: base
    });

    const receipt = await this.publicClient.waitForTransactionReceipt({ hash });

    return {
      ...preflight,
      txHash: hash,
      receipt: JSON.parse(stringify(receipt))
    };
  }

  async preflightContractCall({ to, data, value, valueWei, gasLimit, maxFeePerGasWei, maxPriorityFeePerGasWei }) {
    if (!isAddress(to)) {
      throw new OperatorError('BASE_CONTRACT_INVALID', 'Contrato inválido', { to });
    }
    if (!/^0x[a-fA-F0-9]*$/.test(data)) {
      throw new OperatorError('BASE_CALLDATA_INVALID', 'Calldata inválida', { data });
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
        chain: 'base',
        to,
        valueWei: valueWeiParsed.toString(),
        walletReady: false,
        note: 'BASE_PRIVATE_KEY ausente: dry-run parcial (sem validação de saldo/fee).'
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
      throw new OperatorError('BASE_INSUFFICIENT_BALANCE', 'Saldo insuficiente para contract call', {
        balanceEth: formatEther(balanceWei),
        neededEth: formatEther(estimatedFeeWei + valueWeiParsed)
      });
    }

    return {
      chain: 'base',
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
      chain: base
    });

    const receipt = await this.publicClient.waitForTransactionReceipt({ hash });

    return {
      ...preflight,
      txHash: hash,
      receipt: JSON.parse(stringify(receipt))
    };
  }
}
