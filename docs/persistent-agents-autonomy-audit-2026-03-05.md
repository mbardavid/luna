# Relatório de Auditoria de Agentes Persistentes (2026-03-05)

Escopo: `main/Luna`, `Luan`, `crypto-sage`, `quant-strategist`, `dispatcher` e agente `cto-ops` (se existir).

## 1) Estado atual por agente (resumo operacional)

### Luna (`main`)
- **Definição operacional:** `workspace-main/`
- **Modelo ativo:** `openai-codex/gpt-5.2`
- **Limites/rotina:** dispatch com revisão de risco e rota A2A; rotina de heartbeat enxuta;
  não processa trabalho técnico diretamente.
- **Bindings/channels:** Discord bindings em `openclaw.json` (`id` 1473367119377731800).
- **Papel/falhas:** central do roteamento. No `openclaw.json`, subagentes permitidos são
  `luan`, `crypto-sage`, `quant-strategist`, `playground`, `pg-gpt`, `dispatcher`.

### Luan (`workspace-luan`)
- **Modelo:** `openai-codex/gpt-5.3-codex-spark`
- **Rotinas:** forte disciplina de início/saída (memória, lessons, workflow, testes/relatórios).
- **Falhas recorrentes observadas:** sem divergências estruturais críticas; depende de limites de contexto
  de heartbeat e do funcionamento do `dispatcher` para evitar sobrecarga.

### Crypto-Sage (`workspace-crypto-sage`)
- **Modelo:** `openai-codex/gpt-5.2`
- **Modelo de operação:** dual-plane (control/execution), policy/guardrails no fluxo.
- **Falhas recorrentes potenciais:** logs mostram que rotas de execução são acionadas com ruído de infraestrutura;
  não há erro de binding de agente identificado nesta auditoria.

### Quant-Strategist (`workspace-quant-strategist`)
- **Modelo:** `openai-codex/gpt-5.2`
- **Rotina:** foco de análise PMM + recomendação; não executa trade.
- **Falhas recorrentes observadas:** dependência de arquivo de config PMM (via heartbeat/rotina)
  desalinhada com artefatos existentes em `workspace/polymarket-mm/paper/runs`.

### Dispatcher (`workspace-dispatcher`)
- **Modelo:** `openai-codex/gpt-5.2`
- **Limites no contrato:** permite despachar apenas `luan`, `crypto-sage`, `quant-strategist`;
  não despacha `main`.
- **Falha recorrente relevante:** nenhum erro específico no parser; falhas recorrentes atuais
  parecem vir da infraestrutura abaixo do dispatcher (e.g., gateway health/unreachable + alert noise).

### cto-ops (`workspace/agents/cto-ops`)
- **Existência:** **existe** workspace/documentação (com HEARTBEAT + SOUL/AGENTS/USER + runbooks).
- **Ativo no roteamento:** **não está registrado em `openclaw.json`** (`agents.list`).
- **Impacto:** inconsistente para autonomia operacional futura (alertas/procedimentos existem,
  mas não há caminho oficial de despacho).

## 2) Falhas recorrentes (evidência de logs)

1. **Aviso de plugin obsoleto (config warnings)**
   - Evidência: `heartbeat-v3.log` linha ~7874/7886 etc:
     `plugins.entries.google-antigravity-auth: plugin removed: google-antigravity-auth`
   - Efeito: ruído recorrente de estado e risco de mascarar falhas reais.

2. **PMM restart em loop com config inexistente**
   - Evidência: múltiplas entradas no heartbeat em 2026-03-05: `Phase 1: PMM dead, restart skipped`
     com path `.../polymarket-mm/paper/runs/prod-003.yaml`.
   - Verificação: `prod-003.yaml` não existe; `prod-002.yaml` sim.

3. **Gap de alinhamento de runtime para cto-ops**
   - `openclaw.json` não contém `cto-ops`, apesar de existir docs/rotinas.
   - Isso reduz a autonomia operacional de monitoramento contínuo por arquitetura atual.

## 3) Mudanças aplicadas nesta passada

### A. Configuração de gateway/agents
- Arquivo: `/home/openclaw/.openclaw/openclaw.json`
- Ação: removido `plugins.entries.google-antigravity-auth` (entrada obsoleta).
- Resultado esperado: elimina warning recorrente de config stale e reduz log noise
  sem alterar comportamentos funcionais dos agentes.

### B. Configuração heartbeat/PMM
- Arquivo: `/home/openclaw/.openclaw/workspace/heartbeat-v3/config/v3-config.json`
- Ação: trocado `pmm.default_config` de `prod-003.yaml` (inexistente)
  para `prod-002.yaml` (existente).
- Resultado esperado: encerra tentativas de restart no caminho inválido e melhora utilidade do ciclo
  PMM em saúde automática.

## 4) Propostas de alinhamento futuro (sem rollout automático nesta passada)

1. **Registrar `cto-ops` como agente persistente oficial** (ou retirar formalmente sua ambição)
   - Opção recomendada: criar entrada em `openclaw.json` com SOUL/AGENTS/WORKSPACE alinhados,
     bindings de origem e limites/roteamento apropriados.
2. **Revisar `AGENTS`/runbook cruzado**
   - Unificar que agentes com rotina operativa contínua estejam presentes tanto em `openclaw.json`
     quanto em `AGENTS/HEARTBEAT` e `docs/AGENT_AUDIT_LOG`.
3. **Reduzir ruído de infraestrutura**
   - Padronizar severidade para `gateway unreachable` e criar limiar de alerta por janela para
     evitar retry storms em manutenção normal da rede.
4. **Higiene de configuração persistente**
   - Validar diariamente o `openclaw.json` contra workspaces existentes (`workspace-main`, `workspace-luan`,
     `workspace-crypto-sage`, `workspace-quant-strategist`, `workspace-dispatcher`, `agents/*`).

## 5) Plano de rollout (sugerido)

- **Fase 0 (hoje):** validar mudanças por 24h; confirmar ausência de warnings de plugin e
  ausência de PMM restart por caminho inexistente.
- **Fase 1 (próx. janela):** adicionar `cto-ops` em `openclaw.json` (ou remover formalmente seu pacote)
  e mapear binding mínimo de entrada.
- **Fase 2:** rodar smoke de dispatch/heartbeat com task de prova de rotina + health-gate.
- **Fase 3:** atualizar `docs/AGENT_AUDIT_LOG.md` e `workspace-*/AGENTS.md` com estado final
  de alinhamento (com ID de MC e data).

## Addenda — 2026-03-05 (subtask audit-agents-autonomy-5)

### Estado pós-auditoria (aplicações feitas)

1. `cto-ops` passou a ser registrado em `openclaw.json` (`agents.list`) com workspace
   `/home/openclaw/.openclaw/workspace/agents/cto-ops`.
2. `main` e `dispatcher` mantiveram/receberam `cto-ops` em `subagents.allowAgents` para poderem
   roteá-lo em fluxos internos conforme necessário.
3. `mc-fast-dispatch.sh` passou a mapear UUID `cto-ops-agent-01` para `cto-ops` em tarefas vindas de MC.
4. `heartbeat-v3.py` passou a resolver PMM config com fallback determinístico (`prod-002.yaml`/`prod-001.yaml`)
   quando o caminho primário não existe, reduzindo `PMM dead, restart skipped` por configuração drift.

### Evidência de implementação
- `openclaw.json`
- `scripts/mc-fast-dispatch.sh`
- `workspace/heartbeat-v3/scripts/heartbeat-v3.py`

### Próximo ciclo sugerido (Fase 2)
- Fazer o `smoke` de dispatch para `cto-ops` (via queue file + `mc-fast-dispatch`) após reload de sessão
  para confirmar extração de `session_key`.
- Rodar uma janela de 24h e validar que entradas de PMM em `heartbeat-v3.log` mudaram para o novo
  resolvedor de config (ou que o PMM foi removido do ciclo por `alive`).
