# MEMORY.md

## 2026-02-16

- Matheus prefere automações recorrentes para manutenção do OpenClaw (incluindo updates automáticos e rotina diária de log de memória).
- Rotina-base operacional definida com três frentes: healthcheck diário, update automático diário e logging diário de memória.
- Criado o sub-agente "Luan" especializado em codificação (senior developer).
- Luan possui workspace próprio (`workspace-luan`) e identidade focada em "Code First".
- Luan está vinculado ao grupo de Telegram `-5210149200`.

## 2026-02-17

- Ambiente operacional estável com healthcheck diário validando OpenClaw/browser/Gmail readonly.
- Integração Gemini CLI consolidada via configuração persistente no gateway (`GOOGLE_GENAI_USE_GCA=true`).

## 2026-02-18

- Em instalação OpenClaw não-git, a rotina de auto-update pode executar sem mudança de versão; tratar isso como comportamento esperado e reportar claramente.
- Reinício via `SIGUSR1` segue válido após rotina de update, mantendo continuidade operacional.

## 2026-02-19

- Processo diário de memória passou a consolidar também itens acionáveis por taxonomia (Skills, Tools, Workflows/Rotinas, Docs/Processo) para facilitar manutenção contínua.
- Fechamento diário passou a registrar explicitamente atividade de sessão relevante do período (quando houver), para melhorar rastreabilidade operacional.

## 2026-02-20

- A rotina diária ficou estável com o mesmo padrão operacional (healthcheck + update + logging), reforçando esse trio como baseline de manutenção.
- O tratamento de update em instalação non-git foi consolidado como regra operacional recorrente (comunicar claramente when não houver mudança de versão).

## 2026-02-21

- O fluxo de manutenção diária agora inclui obrigatoriamente uma revisão de taxonomia (Skills/Tools/Workflows), garantindo que aprendizados operacionais sejam movidos para documentação ou configurações permanentes de forma estruturada.
- Ambiente operacional permanece estável (OpenClaw, Browser, Gmail) sob regime de healthcheck matinal.
- Consolidado o uso do cron `daily-memory-24h-log` para automação de logging e taxonomia, reforçando a autonomia na destilação de aprendizados diários.
- **Orquestração A2A Unblocked:** Ativada visibilidade global de sessões e `allowBots` no Discord para permitir que a Luna instrua agentes especializados como o Crypto Sage.
- **Sucesso Operacional:** Concluída a primeira grande operação multi-step delegada: venda de 2 SOL -> bridge para Arbitrum -> depósito na Hyperliquid (total 230 USDC).
- **GitHub Sync Diário:** Automatizada a sincronização do workspace com o repositório GitHub (`mbardavid/luna`) via cron diário, incluindo filtro automático de segredos.
- **Acesso Permanente ao X:** Configurada sessão independente no servidor (`browser-profile-x`) com motor Stealth. O login foi realizado via injeção de `auth_token` e `ct0` capturados via Relay, garantindo acesso 24/7 sem dependência do computador do usuário.
- **NautilusTrader Research:** Clonado o repositório oficial (`nautechsystems/nautilus_trader`) em `research/nautilus_trader`. Iniciada análise de adaptadores para Hyperliquid e Polymarket para futuras estratégias de quant trading e backtesting institucional.
- **Mission Control Integration:** Iniciada a implementação do dashboard centralizado (`openclaw-mission-control`) para visualização de agentes, monitoramento de sinais A2A e orquestração de missões em tempo real.

## 2026-02-22

- **Operações gateway são de alto risco:** Trocar modelo, adicionar provider ou rodar `gateway install --force` com gateway ativo pode causar crash loop. Procedimentos documentados em `memory/lessons.md` com runbooks detalhados.
- **Instalação deve ser exclusivamente no user `openclaw`:** Nunca rodar como root; verificar e remover `/root/.openclaw/` se existir.
- **Modelos Anthropic via Antigravity:** Apenas `claude-opus-4-6-thinking` confirmado funcional. Variantes sem thinking e sonnet retornam 404.

## 2026-02-23

- Período de baixa atividade interativa (22-23 fev). Crons de manutenção operando normalmente.
- Identificados 5 itens de taxonomia operacional pendentes de documentação permanente (runbooks de gateway, policy de instalação, registro de modelos).

## 2026-02-24

- **Mission Control Fase 1 operacional:** Kanban funcional acessível em `142.93.87.36:3000`; plano unificado v2 finalizado; runbook com 8 cenários de teste criado. Board limpo com 8 tasks reais.
- **Diretriz do Matheus: MC deve ser alimentado automaticamente.** Tasks devem ser criadas pela Luna conforme trabalho acontece, sem intervenção humana. Agentes devem aparecer em tempo real.
- **Diretriz do Matheus: sempre avisar antes de ações disruptivas** (gateway restart, model swap, etc.) — nunca surpreender com desconexão.
- **Gateway restart notify hook criado** (`scripts/gateway-restart-notify.sh` + systemd drop-in ExecStartPost) — notifica `#general-luna` automaticamente.
- **Edits concorrentes entre agentes causam conflito:** pg-gpt + Luna editando mesmos arquivos resultou em timeout. Coordenação (locking ou turn-based) é necessária para A2A com edits.
- **Modelo do Luan corrigido** para `claude-opus-4-6-thinking` (modelo Codex anterior falhava com `Unknown model`).
- **Reasoning leak:** `claude-opus-4-6-thinking` vaza thinking blocks no Discord via gateway v2026.2.22-2; problema upstream sem fix do lado do agente.
- **OpenClaw updates globais requerem `sudo`** quando instalação está em `/usr/lib` (não `~/.local/lib`).

## 2026-02-25

- **Lição crítica: reagir automaticamente a falhas de subagentes.** Matheus cobrou que a Luna não investigou/re-spawnou um subagente que deu timeout. Regra nova: quando subagente falha, investigar + agir imediatamente sem esperar o humano perguntar. Documentado em AGENTS.md e lessons.md.
- **Bird CLI instalado** (v0.8.0) — CLI do X/Twitter via GraphQL + cookie auth. Conta @lunabardabot autenticada com auth_token + ct0. Credenciais persistidas em .bashrc + systemd drop-in.
- **Quant Strategist vinculado** ao canal Discord `1475989470883872860`. Binding adicionado via openclaw.json → bindings array.
- **Polymarket Market Maker** — Novo direcionamento do quant-strategist. Matheus quer market maker maker-only no Polymarket. Research consolidado em `research/polymarket-mm-research.md`. Plano detalhado finalizado em `docs/polymarket-mm-plan.md` (GPT 5.2 Pro + Gemini 3 Deep Think). Scaffold do projeto em `polymarket-mm/` com ~15 módulos. Execução sequencial via MC task drain.
- **Spawning rules consolidadas** em `memory/spawning-rules.md` — 5 golden rules para orquestração A2A + MC tracking obrigatório.
- **Plano de estabilidade gateway** documentado após incidente de heap pressure por sessões grandes + tempestade de crons no boot (`docs/stability-plan-crash-2026-02-24.md`).

## 2026-02-25 (tarde)

- **A2A consolidado:** `sessions_spawn` é o padrão pra delegação inter-agentes (fire-and-forget, auto-announce). `sessions_send` só pra mensagens curtas com timeout < 30s. Discord é superfície de output, não orquestração.
- **MC Automation v2 operacional:** `mc-spawn.sh` (prep atômico), `mc-complete.sh` (done + summary), `mc-fail.sh` (retry logic), watchdog com auto-complete. Spec em `docs/mc-automation-upgrade-spec.md`.
- **Crypto-sage workspace consolidado:** Dois workspaces (workspace-crypto-sage + agents/blockchain-operator) fundidos em um. 5 chains no portfolio (Base, Solana, HL, Arbitrum, Polygon).
- **Heartbeat ativado:** 30min, modelo gemini-3-flash. Pre-flight check (cron guard + subagents + MC in_progress) antes de drainer inbox.
- **Responsividade:** Nunca travar turno com timeouts longos. Updates intermediários obrigatórios. MC update é parte do processamento de resultado, não step separado.

## 2026-02-26

- **Gateway crash post-mortem:** Heartbeat AI (gemini-3-flash) causou OOM por retry storm de notificações sem `channel: discord`. User-service desabilitado permanentemente; system-service é agora o único modo de execução. **Regra absoluta: NUNCA parar/reiniciar gateway via exec** — mata a Luna junto.
- **Heartbeat V2 operacional:** Substituiu heartbeat AI por bash puro (`heartbeat-v2.sh` + `gateway-wake-sentinel.sh`). Zero tokens, determinístico, 9 fases incluindo circuit breaker e rate limit. Crontab a cada 10min.
- **PMM pronto para trading:** Wallet com ~228 USDC.e + ~8.38 POL na Polygon, 6 allowances setadas. Próximos passos: P4 (seleção mercados), P5 (paper trading), P6 (produção — requer aprovação Matheus).
- **Token burn lesson:** Flash como agente executor (crypto-sage) entrou em loop de web_fetch 15x → 38M tokens queimados. Agentes especializados com modelo barato devem ter guardrails de loop/retry.

## 2026-02-27

- **PMM iteração paper trading amadureceu:** Ciclo completo detecta→analisa→spawna fix→re-run emergiu organicamente (run-001 a run-003). 4 bugs fundamentais do quoting engine identificados e fixados pelo Luan (spread calibration, one-sided inventory, weak skew, wallet exhaustion). run-004 aguardando start.
- **SELL position sync fix consolidado:** PaperVenue agora faz resize + complement routing (SELL YES → BUY NO quando posição=0). Pattern reutilizável para qualquer venue simulado.
- **Gateway post-crash estável por 10h+:** Após 5 restarts em 26/fev (crash chain + updates), sistema se manteve estável a noite inteira. Heartbeat V2 (bash puro) validado em produção.
- **Operacional: `active-tasks.md` precisa de cleanup periódico** — ficou 2 semanas desatualizado. Incluir na rotina de manutenção.
