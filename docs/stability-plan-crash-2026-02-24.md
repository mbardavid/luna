# Plano de Estabilidade — Incidente “gateway/Discord não responde” (madrugada 2026-02-24)

## Diagnóstico (confirmado por evidência local)

### A) Pressão de memória por sessões gigantes
- Arquivos `.jsonl` de sessões acumulados (alguns multi-MB) são carregados/parseados no startup.
- Mesmo 7–8MB em disco pode expandir para centenas de MB de heap no Node.
- Evidência: existem sessões arquivadas grandes em `~/.openclaw/agents/*/sessions/archived/*.jsonl` e ainda há sessões grandes ativas.

### B) Tempestade de crons no boot + update via npm
- Jobs com `nextRunAtMs` no passado + `runningAtMs` pendente tentaram rodar simultaneamente no boot.
- O job de update (`npm i -g openclaw@latest`) consumiu muita RAM/CPU e degradou o event loop.
- Evidência: `journalctl` mostra timeouts do `DiscordMessageListener` (30s) e systemd forçando SIGKILL após timeout de parada; `systemctl status` mostra processos `npm i -g` dentro do cgroup do gateway.

## Objetivo
Evitar repetição do colapso: boot precisa estabilizar rápido e não pode disparar carga pesada em paralelo.

## Plano (3 camadas)

### 1) Higiene de sessões (prevenção de heap blowup)
1.1 **Archiving/compaction automático**
- Criar uma rotina diária que:
  - identifica sessões `.jsonl` > 1MB
  - se inativas há X dias (ex: 7), move para `archived/` e/ou compacta

1.2 **Limites e guardrails**
- Configurar compaction (modo safeguard) para novas sessões.
- Definir limite operacional: "sessão > 1MB" vira alerta + auto-archive.

### 2) Crons: stagger + limpeza de estado pendente
2.1 **Stagger no boot**
- Garantir que jobs não rodem “imediatamente” no startup.
- Regras:
  - qualquer job com `nextRunAtMs` no passado → adiar + jitter (ex: +10min + random 0–5min)

2.2 **Sanitização de `runningAtMs`**
- Ao iniciar gateway, se `runningAtMs` está setado mas não há execução em curso → limpar.

### 3) Auto-update seguro (sem derrubar o runtime)
3.1 **Janela fixa**
- Rodar update em horário de baixa (ex: 04:00 BRT).

3.2 **RAM guard**
- Só executar update se `MemAvailable` > 1.5GB (ajustável).

3.3 **Isolamento**
- Ideal: update fora do processo do gateway (systemd timer/serviço separado), para não bloquear o event loop.

## Critérios objetivos de validação
- Boot com gateway online e Discord respondendo em < 60s.
- Sem logs de `DiscordMessageListener timed out` em 1h após boot.
- Sem `systemd: State 'stop-sigterm' timed out`.
- Memória do gateway < 500MB em idle; swap não volta a 100%.
- Zero “tempestade” de crons: no máximo 1 job pesado por vez.

## Próximos passos propostos
- (P1) Criar runbook + scripts de auditoria (read-only) para listar sessões >1MB e crons atrasados.
- (P2) Propor cron/timer para archive/compaction.
- (P3) Migrar update para janela fixa + RAM guard + execução fora do gateway.
