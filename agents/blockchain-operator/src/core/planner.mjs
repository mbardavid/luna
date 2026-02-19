function pushLiveExecute(steps, dryRun, step) {
  if (!dryRun) {
    steps.push(step);
  }
}

function pushConfirmationStep(steps, { required, riskClassification, riskClassificationSource }) {
  if (!required) {
    return;
  }

  steps.push({
    id: 'require-human-confirmation',
    type: 'confirm',
    connector: 'operator',
    riskClassification: riskClassification ?? null,
    riskClassificationSource: riskClassificationSource ?? null
  });
}

export function buildPlan(intent, { dryRun, confirmation } = {}) {
  const mode = dryRun ? 'dry-run' : 'live';
  const steps = [
    {
      id: 'validate-policy',
      type: 'guardrail'
    }
  ];

  pushConfirmationStep(steps, confirmation ?? {});

  if (intent.action === 'portfolio_balance') {
    steps.push({ id: 'fetch-balances', type: 'read', connector: 'portfolio' });
    steps.push({ id: 'mark-to-market', type: 'valuation', connector: 'portfolio' });
    steps.push({ id: 'format-discord-response', type: 'report', connector: 'portfolio' });
  }

  if (intent.action === 'transfer' || intent.action === 'send') {
    steps.push({ id: 'preflight-transfer', type: 'preflight', connector: intent.chain });
    pushLiveExecute(steps, dryRun, { id: 'execute-transfer', type: 'execute', connector: intent.chain });
  }

  if (intent.action === 'contract_call') {
    steps.push({ id: 'preflight-contract-call', type: 'preflight', connector: 'base' });
    pushLiveExecute(steps, dryRun, { id: 'execute-contract-call', type: 'execute', connector: 'base' });
  }

  if (intent.action === 'hl_order') {
    steps.push({ id: 'preflight-hl-order', type: 'preflight', connector: 'hyperliquid' });
    pushLiveExecute(steps, dryRun, { id: 'execute-hl-order', type: 'execute', connector: 'hyperliquid' });
  }

  if (intent.action === 'hl_cancel') {
    steps.push({ id: 'preflight-hl-cancel', type: 'preflight', connector: 'hyperliquid' });
    pushLiveExecute(steps, dryRun, { id: 'execute-hl-cancel', type: 'execute', connector: 'hyperliquid' });
  }

  if (intent.action === 'hl_modify') {
    steps.push({ id: 'preflight-hl-modify', type: 'preflight', connector: 'hyperliquid' });
    pushLiveExecute(steps, dryRun, { id: 'execute-hl-modify', type: 'execute', connector: 'hyperliquid' });
  }

  if (intent.action === 'hl_deposit') {
    steps.push({ id: 'preflight-hl-deposit', type: 'preflight', connector: 'hyperliquid' });
    pushLiveExecute(steps, dryRun, { id: 'execute-hl-deposit', type: 'execute', connector: 'hyperliquid' });
  }

  if (intent.action === 'bridge') {
    steps.push({ id: 'quote-bridge', type: 'preflight', connector: 'debridge' });
    pushLiveExecute(steps, dryRun, { id: 'execute-bridge', type: 'execute', connector: intent.fromChain });
  }

  if (intent.action === 'swap_jupiter') {
    steps.push({ id: 'preflight-swap-jupiter', type: 'preflight', connector: 'jupiter' });
    pushLiveExecute(steps, dryRun, { id: 'execute-swap-jupiter', type: 'execute', connector: 'jupiter' });
  }

  if (intent.action === 'swap_raydium') {
    steps.push({ id: 'preflight-swap-raydium', type: 'preflight', connector: 'raydium' });
    pushLiveExecute(steps, dryRun, { id: 'execute-swap-raydium', type: 'execute', connector: 'raydium' });
  }

  if (intent.action === 'swap_pumpfun') {
    steps.push({ id: 'preflight-swap-pumpfun', type: 'preflight', connector: 'pumpfun' });
    pushLiveExecute(steps, dryRun, { id: 'execute-swap-pumpfun', type: 'execute', connector: 'pumpfun' });
  }

  if (intent.action === 'defi_deposit') {
    steps.push({ id: 'preflight-defi-deposit', type: 'preflight', connector: 'defi' });
    pushLiveExecute(steps, dryRun, { id: 'execute-defi-deposit', type: 'execute', connector: 'defi' });
  }

  if (intent.action === 'defi_withdraw') {
    steps.push({ id: 'preflight-defi-withdraw', type: 'preflight', connector: 'defi' });
    pushLiveExecute(steps, dryRun, { id: 'execute-defi-withdraw', type: 'execute', connector: 'defi' });
  }

  return {
    mode,
    steps
  };
}
