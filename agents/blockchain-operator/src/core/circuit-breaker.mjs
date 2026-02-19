import { readJson, writeJson } from '../utils/fs.mjs';
import { fromRoot } from '../utils/paths.mjs';
import { OperatorError } from '../utils/errors.mjs';

const BREAKER_PATH = fromRoot('state', 'circuit-breaker.json');

function loadState() {
  return readJson(BREAKER_PATH, {
    failures: [],
    openUntil: null,
    lastError: null,
    updatedAt: null
  });
}

function saveState(state) {
  state.updatedAt = new Date().toISOString();
  writeJson(BREAKER_PATH, state);
}

function pruneFailures(state, windowSec) {
  const now = Date.now();
  const windowMs = windowSec * 1000;
  state.failures = state.failures.filter((ts) => now - ts <= windowMs);
}

export function assertCanExecute(policy) {
  if (!policy.circuitBreaker.enabled) return;

  const state = loadState();
  if (state.openUntil && Date.now() < state.openUntil) {
    throw new OperatorError('CIRCUIT_BREAKER_OPEN', 'Circuit breaker está aberto', {
      openUntil: new Date(state.openUntil).toISOString(),
      lastError: state.lastError
    });
  }
}

export function registerFailure(policy, error) {
  if (!policy.circuitBreaker.enabled) return;

  const state = loadState();
  pruneFailures(state, policy.circuitBreaker.windowSec);
  state.failures.push(Date.now());
  state.lastError = {
    code: error.code ?? 'UNKNOWN',
    message: error.message ?? String(error)
  };

  if (state.failures.length >= policy.circuitBreaker.maxFailures) {
    state.openUntil = Date.now() + policy.circuitBreaker.cooldownSec * 1000;
  }

  saveState(state);
}

export function registerSuccess(policy) {
  if (!policy.circuitBreaker.enabled) return;

  const state = loadState();
  pruneFailures(state, policy.circuitBreaker.windowSec);
  saveState(state);
}
