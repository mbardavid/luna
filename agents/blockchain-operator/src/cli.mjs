#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import 'dotenv/config';
import { parseInstruction } from './core/parser.mjs';
import { normalizeIntent } from './core/normalize.mjs';
import { loadPolicy, PolicyEngine } from './core/policy-engine.mjs';
import { buildPlan } from './core/planner.mjs';
import { computeIdempotencyKey } from './core/idempotency-store.mjs';
import { runInstruction, runExecutionPayload, runNativeCommand } from './core/executor.mjs';
import { getRunEvents, getAuditPath } from './core/audit-log.mjs';
import { HyperliquidConnector } from './connectors/hyperliquid.mjs';
import { OperatorError } from './utils/errors.mjs';

function parseArgs(argv) {
  const flags = {};
  const positional = [];

  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith('--')) {
      positional.push(token);
      continue;
    }

    const key = token.slice(2);
    const next = argv[i + 1];

    if (!next || next.startsWith('--')) {
      flags[key] = true;
      continue;
    }

    flags[key] = next;
    i += 1;
  }

  return { flags, positional };
}

function getInstruction(flags, positional) {
  if (flags.instruction) return String(flags.instruction);
  if (positional.length > 0) return positional.join(' ');
  throw new OperatorError('INSTRUCTION_REQUIRED', 'Informe --instruction "..."');
}

function getNativeCommand(flags, positional) {
  if (flags.command) return String(flags.command);
  if (positional.length > 0) return positional.join(' ');
  throw new OperatorError('NATIVE_COMMAND_REQUIRED', 'Informe --command <nome> (ex.: saldo)');
}

function parseJson(source, raw) {
  try {
    return JSON.parse(raw);
  } catch (error) {
    throw new OperatorError('JSON_PARSE_ERROR', `Falha ao parsear JSON (${source})`, {
      message: error.message
    });
  }
}

async function readStdinText() {
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString('utf8').trim();
}

async function getExecutionPayload(flags, positional) {
  if (flags.payload) {
    return parseJson('--payload', String(flags.payload));
  }

  if (flags['payload-file']) {
    const resolved = path.resolve(String(flags['payload-file']));
    if (!fs.existsSync(resolved)) {
      throw new OperatorError('PAYLOAD_FILE_NOT_FOUND', `Arquivo de payload não encontrado: ${resolved}`);
    }

    const raw = fs.readFileSync(resolved, 'utf8');
    return parseJson(resolved, raw);
  }

  if (positional.length === 1 && positional[0].trim().startsWith('{')) {
    return parseJson('positional', positional[0]);
  }

  if (flags.stdin || !process.stdin.isTTY) {
    const raw = await readStdinText();
    if (!raw) {
      throw new OperatorError('PAYLOAD_STDIN_EMPTY', 'STDIN vazio. Envie JSON válido para execution plane.');
    }

    return parseJson('stdin', raw);
  }

  throw new OperatorError(
    'EXECUTION_PAYLOAD_REQUIRED',
    'Informe payload via --payload, --payload-file ou stdin para execution plane.'
  );
}

function printHelp() {
  console.log(`
OpenClaw Blockchain Operator CLI

Dual-plane:
  - Control plane  : entrada NL PT/EN (humano -> bot)
  - Execution plane: entrada JSON estruturada (bot -> bot)

Commands:
  plan            --instruction "..." [--policy <path>] [--dry-run]
  execute         --instruction "..." [--policy <path>] [--dry-run] [--idempotency-key <key>]
  execute-native  --command <name> [--policy <path>] [--dry-run] [--idempotency-key <key>]
  execute-plane   [--payload '<json>'] [--payload-file <path>] [--stdin] [--policy <path>] [--dry-run] [--idempotency-key <key>]
  replay          --run-id <RUN_ID>

Examples:
  node src/cli.mjs plan --instruction "enviar 0.001 ETH para 0x... na base"
  node src/cli.mjs execute --instruction "send 0.01 SOL to <ADDR> on solana" --dry-run
  node src/cli.mjs execute --instruction "/saldo" --dry-run
  node src/cli.mjs execute-native --command saldo --dry-run

  node src/cli.mjs execute-plane --payload-file docs/examples/a2a-v1/transfer.json --policy config/policy.live.json
  cat payload.json | node src/cli.mjs execute-plane --stdin --policy config/policy.live.json

  node src/cli.mjs replay --run-id run_123

Security:
  - Execution plane live pode exigir auth assinada (A2A_SECURITY_MODE / A2A_HMAC_KEYS_JSON).
`);
}

async function handlePlan(flags, positional) {
  const instruction = getInstruction(flags, positional);
  const policyLoaded = loadPolicy(flags.policy ? String(flags.policy) : null);
  const policy = policyLoaded.data;

  const parsed = parseInstruction(instruction);
  const canonicalIntent = normalizeIntent(parsed);
  const dryRun = Boolean(flags['dry-run']) || policy.execution.defaultDryRun;

  let intent = canonicalIntent;
  if (['hl_order', 'hl_modify'].includes(canonicalIntent.action)) {
    intent = await new HyperliquidConnector({}).enrichIntentForPolicy(canonicalIntent, policy);
  }

  const policyResult = new PolicyEngine(policy).evaluate(intent, { isDryRun: dryRun });
  const plan = buildPlan(intent, { dryRun });
  const idempotencyKey = computeIdempotencyKey(canonicalIntent, policy.version);

  console.log(
    JSON.stringify(
      {
        ok: true,
        plane: 'control',
        policyPath: policyLoaded.path,
        dryRun,
        parsed,
        intent,
        canonicalIntent,
        policyResult,
        plan,
        idempotencyKey
      },
      null,
      2
    )
  );
}

async function handleExecute(flags, positional) {
  const instruction = getInstruction(flags, positional);
  const result = await runInstruction({
    instruction,
    policyPath: flags.policy ? String(flags.policy) : null,
    dryRun: Boolean(flags['dry-run']),
    idempotencyKey: flags['idempotency-key'] ? String(flags['idempotency-key']) : null
  });

  console.log(JSON.stringify(result, null, 2));
  process.exitCode = result.ok ? 0 : 1;
}

async function handleExecuteNative(flags, positional) {
  const command = getNativeCommand(flags, positional);

  const result = await runNativeCommand({
    command,
    policyPath: flags.policy ? String(flags.policy) : null,
    dryRun: Boolean(flags['dry-run']),
    idempotencyKey: flags['idempotency-key'] ? String(flags['idempotency-key']) : null
  });

  console.log(JSON.stringify(result, null, 2));
  process.exitCode = result.ok ? 0 : 1;
}

async function handleExecutePlane(flags, positional) {
  const payload = await getExecutionPayload(flags, positional);

  const result = await runExecutionPayload({
    payload,
    policyPath: flags.policy ? String(flags.policy) : null,
    dryRun: Boolean(flags['dry-run']),
    idempotencyKey: flags['idempotency-key'] ? String(flags['idempotency-key']) : null
  });

  console.log(JSON.stringify(result, null, 2));
  process.exitCode = result.ok ? 0 : 1;
}

async function handleReplay(flags) {
  const runId = flags['run-id'];
  if (!runId) {
    throw new OperatorError('RUN_ID_REQUIRED', 'Informe --run-id <RUN_ID>');
  }

  const events = getRunEvents(String(runId));
  console.log(
    JSON.stringify(
      {
        ok: true,
        runId,
        auditPath: getAuditPath(),
        events
      },
      null,
      2
    )
  );
}

async function main() {
  const [, , command, ...rest] = process.argv;
  if (!command || command === 'help' || command === '--help' || command === '-h') {
    printHelp();
    return;
  }

  const { flags, positional } = parseArgs(rest);

  if (command === 'plan') {
    await handlePlan(flags, positional);
    return;
  }

  if (command === 'execute') {
    await handleExecute(flags, positional);
    return;
  }

  if (command === 'execute-native') {
    await handleExecuteNative(flags, positional);
    return;
  }

  if (command === 'execute-plane') {
    await handleExecutePlane(flags, positional);
    return;
  }

  if (command === 'replay') {
    await handleReplay(flags);
    return;
  }

  throw new OperatorError('COMMAND_UNKNOWN', `Comando desconhecido: ${command}`);
}

main().catch((error) => {
  const payload = {
    ok: false,
    error: {
      code: error.code ?? 'CLI_ERROR',
      message: error.message,
      details: error.details ?? null
    }
  };

  console.error(JSON.stringify(payload, null, 2));
  process.exitCode = 1;
});
