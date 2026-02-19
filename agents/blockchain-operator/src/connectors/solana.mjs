import bs58 from 'bs58';
import {
  Connection,
  Keypair,
  LAMPORTS_PER_SOL,
  PublicKey,
  SystemProgram,
  Transaction,
  VersionedTransaction,
  sendAndConfirmTransaction
} from '@solana/web3.js';
import { OperatorError } from '../utils/errors.mjs';

function parseSecretKey() {
  const b58 = process.env.SOLANA_PRIVATE_KEY_B58;
  const json = process.env.SOLANA_PRIVATE_KEY_JSON;

  if (!b58 && !json) return null;

  try {
    if (b58) {
      const bytes = bs58.decode(b58.trim());
      return Uint8Array.from(bytes);
    }

    const arr = JSON.parse(json);
    if (!Array.isArray(arr)) {
      throw new Error('SOLANA_PRIVATE_KEY_JSON deve ser array numérico');
    }
    return Uint8Array.from(arr);
  } catch (error) {
    throw new OperatorError('SOLANA_KEY_PARSE_ERROR', 'Falha ao ler chave Solana', {
      message: error.message
    });
  }
}

function toLamports(amount) {
  const lamports = Math.round(Number(amount) * LAMPORTS_PER_SOL);
  if (!Number.isFinite(lamports) || lamports <= 0) {
    throw new OperatorError('SOLANA_AMOUNT_INVALID', 'Amount inválido para Solana', { amount });
  }
  return lamports;
}

function deserializeSerializedTransaction(transactionBase64) {
  let bytes;

  try {
    bytes = Buffer.from(transactionBase64, 'base64');
  } catch (error) {
    throw new OperatorError('SOLANA_TX_BASE64_INVALID', 'Transação serializada inválida (base64)', {
      message: error.message
    });
  }

  if (!bytes || bytes.length === 0) {
    throw new OperatorError('SOLANA_TX_EMPTY', 'Transação serializada vazia');
  }

  try {
    return {
      kind: 'versioned',
      tx: VersionedTransaction.deserialize(bytes)
    };
  } catch {
    try {
      return {
        kind: 'legacy',
        tx: Transaction.from(bytes)
      };
    } catch (error) {
      throw new OperatorError('SOLANA_TX_DESERIALIZE_FAILED', 'Falha ao desserializar transação Solana', {
        message: error.message
      });
    }
  }
}

function signerIsRequired({ tx, kind, signerPubkey }) {
  if (kind === 'versioned') {
    const staticKeys = tx.message.staticAccountKeys ?? [];
    const requiredSigners = tx.message.header?.numRequiredSignatures ?? 0;

    for (let i = 0; i < requiredSigners; i += 1) {
      if (staticKeys[i]?.toBase58?.() === signerPubkey) {
        return true;
      }
    }

    return false;
  }

  return tx.signatures.some((s) => s.publicKey?.toBase58?.() === signerPubkey);
}

function summarizeSimulation(simulation) {
  return {
    err: simulation?.value?.err ?? null,
    logs: simulation?.value?.logs ?? [],
    unitsConsumed: simulation?.value?.unitsConsumed ?? null,
    returnData: simulation?.value?.returnData ?? null
  };
}

export class SolanaConnector {
  constructor({ rpcUrl } = {}) {
    this.rpcUrl = rpcUrl ?? process.env.SOLANA_RPC_URL ?? 'https://api.mainnet-beta.solana.com';
    this.connection = new Connection(this.rpcUrl, 'confirmed');

    const secret = parseSecretKey();
    this.keypair = secret ? Keypair.fromSecretKey(secret) : null;
  }

  getAddress() {
    return this.keypair ? this.keypair.publicKey.toBase58() : null;
  }

  ensureWallet() {
    if (!this.keypair) {
      throw new OperatorError(
        'SOLANA_KEY_MISSING',
        'SOLANA_PRIVATE_KEY_B58 ou SOLANA_PRIVATE_KEY_JSON não configurada.'
      );
    }
  }

  ensureRecipientAddress(address, field = 'recipient') {
    if (!address) return null;

    try {
      return new PublicKey(address).toBase58();
    } catch {
      throw new OperatorError('SOLANA_ADDRESS_INVALID', `${field} Solana inválido`, {
        field,
        address
      });
    }
  }

  async preflightNativeTransfer({ to, amount }) {
    let destination;
    try {
      destination = new PublicKey(to);
    } catch {
      throw new OperatorError('SOLANA_ADDRESS_INVALID', 'Endereço Solana inválido', { to });
    }

    if (!this.keypair) {
      return {
        chain: 'solana',
        to: destination.toBase58(),
        valueSol: String(Number(amount)),
        walletReady: false,
        note: 'SOLANA_PRIVATE_KEY ausente: dry-run parcial (sem validação de saldo/fee).'
      };
    }

    const from = this.keypair.publicKey;
    const lamports = toLamports(amount);

    const [balance, latestBlockhash] = await Promise.all([
      this.connection.getBalance(from),
      this.connection.getLatestBlockhash('confirmed')
    ]);

    const tx = new Transaction({
      feePayer: from,
      recentBlockhash: latestBlockhash.blockhash
    }).add(
      SystemProgram.transfer({
        fromPubkey: from,
        toPubkey: destination,
        lamports
      })
    );

    const feeInfo = await this.connection.getFeeForMessage(tx.compileMessage(), 'confirmed');
    const feeLamports = feeInfo.value ?? 5000;

    if (balance < lamports + feeLamports) {
      throw new OperatorError('SOLANA_INSUFFICIENT_BALANCE', 'Saldo SOL insuficiente para valor + fee', {
        balanceSol: balance / LAMPORTS_PER_SOL,
        neededSol: (lamports + feeLamports) / LAMPORTS_PER_SOL
      });
    }

    return {
      chain: 'solana',
      from: from.toBase58(),
      to: destination.toBase58(),
      valueSol: String(Number(amount)),
      lamports,
      estimatedFeeLamports: feeLamports,
      balanceSol: balance / LAMPORTS_PER_SOL
    };
  }

  async sendNativeTransfer({ to, amount }) {
    this.ensureWallet();
    const preflight = await this.preflightNativeTransfer({ to, amount });

    const destination = new PublicKey(to);
    const latestBlockhash = await this.connection.getLatestBlockhash('confirmed');

    const tx = new Transaction({
      feePayer: this.keypair.publicKey,
      recentBlockhash: latestBlockhash.blockhash
    }).add(
      SystemProgram.transfer({
        fromPubkey: this.keypair.publicKey,
        toPubkey: destination,
        lamports: toLamports(amount)
      })
    );

    const signature = await sendAndConfirmTransaction(this.connection, tx, [this.keypair], {
      commitment: 'confirmed'
    });

    return {
      ...preflight,
      txHash: signature,
      explorer: `https://solscan.io/tx/${signature}`
    };
  }

  async preflightSerializedTransaction({ transactionBase64, label = 'solana.tx' }) {
    const parsed = deserializeSerializedTransaction(transactionBase64);

    if (!this.keypair) {
      return {
        chain: 'solana',
        action: label,
        walletReady: false,
        txType: parsed.kind,
        note: 'SOLANA_PRIVATE_KEY ausente: dry-run parcial (sem simulação assinada).'
      };
    }

    const signer = this.keypair.publicKey.toBase58();
    const signerNeeded = signerIsRequired({ tx: parsed.tx, kind: parsed.kind, signerPubkey: signer });

    if (signerNeeded) {
      if (parsed.kind === 'versioned') {
        parsed.tx.sign([this.keypair]);
      } else {
        parsed.tx.partialSign(this.keypair);
      }
    }

    let simulation;

    try {
      simulation = await this.connection.simulateTransaction(parsed.tx, {
        commitment: 'confirmed',
        sigVerify: signerNeeded,
        replaceRecentBlockhash: true
      });
    } catch (error) {
      const message = String(error?.message ?? '').toLowerCase();

      // Newer Solana RPC nodes reject sigVerify when replaceRecentBlockhash is enabled.
      // Retry once with sigVerify disabled while still refreshing blockhash for simulation.
      if (!message.includes('sigverify may not be used with replacerecentblockhash')) {
        throw error;
      }

      simulation = await this.connection.simulateTransaction(parsed.tx, {
        commitment: 'confirmed',
        sigVerify: false,
        replaceRecentBlockhash: true
      });
    }

    const summary = summarizeSimulation(simulation);

    if (summary.err) {
      throw new OperatorError('SOLANA_TX_SIMULATION_FAILED', 'Simulação da transação falhou', {
        action: label,
        simulation: summary
      });
    }

    return {
      chain: 'solana',
      action: label,
      walletReady: true,
      txType: parsed.kind,
      signerNeeded,
      simulation: summary
    };
  }

  async sendSerializedTransaction({
    transactionBase64,
    label = 'solana.tx',
    skipPreflight = false,
    maxRetries = 3
  }) {
    this.ensureWallet();

    const parsed = deserializeSerializedTransaction(transactionBase64);
    const signer = this.keypair.publicKey.toBase58();
    const signerNeeded = signerIsRequired({ tx: parsed.tx, kind: parsed.kind, signerPubkey: signer });

    if (!signerNeeded) {
      throw new OperatorError('SOLANA_TX_SIGNER_NOT_REQUIRED', 'Transação não requer a wallet local como signer', {
        action: label,
        signer
      });
    }

    const latestBlockhash = await this.connection.getLatestBlockhash('confirmed');

    if (parsed.kind === 'versioned') {
      if (parsed.tx.message && 'recentBlockhash' in parsed.tx.message) {
        parsed.tx.message.recentBlockhash = latestBlockhash.blockhash;
      }
      parsed.tx.sign([this.keypair]);
    } else {
      parsed.tx.recentBlockhash = latestBlockhash.blockhash;
      parsed.tx.partialSign(this.keypair);
    }

    const serialized = parsed.tx.serialize();
    const signature = await this.connection.sendRawTransaction(serialized, {
      skipPreflight,
      maxRetries
    });

    const confirmation = await this.connection.confirmTransaction(signature, 'confirmed');

    if (confirmation?.value?.err) {
      throw new OperatorError('SOLANA_TX_CONFIRMATION_FAILED', 'Falha na confirmação da transação Solana', {
        action: label,
        signature,
        err: confirmation.value.err
      });
    }

    return {
      chain: 'solana',
      action: label,
      txType: parsed.kind,
      txHash: signature,
      explorer: `https://solscan.io/tx/${signature}`,
      confirmation
    };
  }
}
