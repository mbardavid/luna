import { appendJsonl, readJsonLines } from '../utils/fs.mjs';
import { fromRoot } from '../utils/paths.mjs';

const AUDIT_PATH = fromRoot('state', 'audit.jsonl');

export function logEvent({ runId, event, data }) {
  appendJsonl(AUDIT_PATH, {
    ts: new Date().toISOString(),
    runId,
    event,
    data
  });
}

export function getRunEvents(runId) {
  return readJsonLines(AUDIT_PATH).filter((row) => row.runId === runId);
}

export function getAuditPath() {
  return AUDIT_PATH;
}
