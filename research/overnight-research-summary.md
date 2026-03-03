# Overnight Research Summary — 2026-03-03

Status: running autonomous research loop (max 2 Quant tasks in parallel).

## Scope
- Polymarket Rewards Farming (mechanics + market selection + sizing)
- Event-driven trading (mispricing detection + Kelly sizing)
- DeFi yield (Base/Solana) + reality checks
- SOL liquid staking composability
- New: MEV, funding-rate arb, emergent strategies
- Competitive intel: Polymarket power users + MM landscape

## Run log
- 03:18 UTC — Loop started.

## Findings (rolling)

### A1 — Polymarket Rewards Farming Mechanics (2026-03-03)
- **Docs oficiais** confirmam fórmula de scoring e elegibilidade: utilidade quadrática por spread `S(v,s)=((v-s)/v)^2*b` + **boost two-sided** via `Qmin` e penalidade single-sided `c=3.0`.
- **Amostragem por minuto**: 10,080 samples/epoch; payout diário ~00:00 UTC; mínimo $1.
- **Params por mercado**: Gamma expõe `rewardsMinSize` e `rewardsMaxSpread` no objeto do mercado.
- **Validação empírica**: usar endpoint `/order-scoring` (e endpoints autenticados de rewards) para checar se nossas ordens estão pontuando — sem depender de “achismo”.
- **Ação recomendada**: integrar no bot um “eligibility probe” que bate `/order-scoring` e ajusta quote cadence/sizing para maximizar score.

Arquivo completo: `research/deep-dive-polymarket-rewards-farming.md`.
