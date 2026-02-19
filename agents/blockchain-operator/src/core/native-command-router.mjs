import { OperatorError } from '../utils/errors.mjs';

const PORTFOLIO_BALANCE_ALIASES = new Set([
  'saldo',
  '/saldo',
  'balance',
  '/balance',
  'balances',
  '/balances'
]);

function normalizeCommandName(value) {
  return String(value ?? '')
    .trim()
    .toLowerCase();
}

export function resolveNativeCommand(command) {
  const rawName = typeof command === 'string' ? command : command?.name;
  const normalized = normalizeCommandName(rawName);

  if (!normalized) {
    throw new OperatorError('NATIVE_COMMAND_REQUIRED', 'Informe o nome do comando nativo (ex.: saldo).');
  }

  if (PORTFOLIO_BALANCE_ALIASES.has(normalized)) {
    return {
      command: 'saldo',
      action: 'portfolio_balance',
      resolvedInstruction: '/saldo',
      route: 'portfolio_balance_v2'
    };
  }

  throw new OperatorError(
    'NATIVE_COMMAND_UNSUPPORTED',
    `Comando nativo não suportado: ${normalized}`,
    {
      command: normalized,
      supported: ['saldo']
    }
  );
}
