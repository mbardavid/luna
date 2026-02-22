# MEMORY.md — Long-term Notes (Operator Agent)

## Mandato permanente

- Agente **executor-only** para operações blockchain/DEX.
- Estratégia, direção e sizing ficam fora deste agente.
- Arquitetura operacional padrão: **dual-plane** (control + execution).

## Rotina persistente de memória e aprendizado

1. Registrar eventos do dia em `memory/YYYY-MM-DD.md`.
2. Manter `memory/active-tasks.md` como fonte da retomada de sessão.
3. Consolidar erros recorrentes e correções em `memory/lessons.md`.
4. Registrar novos workflows/skills em `memory/workflow-registry.md`.
5. Atualizar `TOOLS.md` e skills específicas sempre que o workflow depender de setup local ou procedimento técnico novo.
6. Revisar periodicamente logs diários e promover para este arquivo apenas o que for conhecimento estável de longo prazo.

## Decisões consolidadas

### 2026-02-17

- Foundation criada para agente operador blockchain executor-only.
- Conectores MVP priorizados: Base transfer, Solana transfer, Hyperliquid info/dry-run.
- Guardrails estruturados em policy parametrizável (valores podem ser definidos depois).
- Estratégia operacional: dry-run por default até `policy.live.json` habilitar execução.
- Fase B concluída: Hyperliquid live executor (spot/perp + cancel/modify), assinatura via SDK oficial Python, coordenação de nonce local por signer e pre-checks de margem/posição.
- Canal Discord operacional confirmado para integração: `1473392629055098942`.
- Decisões finais de policy: key segregation obrigatória (Base/Solana/HL), Hyperliquid como destination_l3, bridge via deBridge (source Base/Solana), recipients whitelisted, sem limites numéricos por ora, price source Chainlink+Pyth fallback, reporting por operação + resumo diário.

### 2026-02-18

- Paridade total de memória contínua implantada com o padrão raiz.
- `memory/workflow-registry.md` adotado como registro oficial de workflows/skills do agente.
- Rotina diária de aprendizado formalizada em `AGENTS.md` e `HEARTBEAT.md`.

### 2026-02-21

- Incorporação de delegação A2A (Luna) para acesso a ferramentas externas (GitHub, Vercel, Supabase).
- Automação da rotina de taxonomia e log de memória diária via cron job.
- Sucesso na primeira grande operação multi-step live: swap 2 SOL (Jupiter) -> bridge Arbitrum -> deposit Hyperliquid, resultando em saldo final de 230 USDC na HL.

### 2026-02-22

- Estabilização da rotina de memória e taxonomia automática via cron.
- Continuidade da operação multi-chain (Solana/Arbitrum/Hyperliquid) confirmada e logada.
- Refinamento do rastreio de delegação vinda de outros agentes (Luna) para controle de workflows internos.
