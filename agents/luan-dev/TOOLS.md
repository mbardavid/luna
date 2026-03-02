# TOOLS.md — Luan Dev

## Ambiente local
- `OPENCLAW_BIN`: executável principal do gateway (padrão `openclaw`)
- `MC_CLIENT`: script `scripts/mc-client.sh`
- `MC_BASE_URL` / `MC_AUTH_TOKEN`: configuração de Mission Control
- Diretório raiz deste agente: `agents/luan-dev/`

## Aliases úteis
- `bash scripts/mc-spawn.sh --agent luan-dev --title ... --task ... --json`
- `bash scripts/mc-task-update.sh --task-id <id> --input <payload>`
- `bash scripts/mc-complete.sh --task-id <id> --summary "Concluído"`

## Pontos de observação
- Script principal de contrato de revisão: `docs/agentic-loop-contract.md`
- Estado de orquestração: `agents/luan-dev/memory/orchestration-state.json`
- Política de risco: `config/cto-risk-policy.json`
