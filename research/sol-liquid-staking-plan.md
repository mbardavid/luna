# PLANO DE EXECUÇÃO — Liquid Staking de ~US$182 em SOL (JitoSOL vs mSOL)

**Data:** 2026-03-05  
**Objetivo:** escolher entre JitoSOL e mSOL para manter LST composable (Kamino/MarginFi/Drift), com custo/risco baixos e sem executar nada on-chain nesta fase.

## 0) Recomendação principal

**Recomendado para começar:** **~85–90% mSOL + 10–15% JitoSOL** (ou **100% mSOL** para máxima simplicidade).

**Racional:**
- mSOL tem histórico mais amplo de liquidez e adoção em apps DeFi de Solana; menor atrito operacional para ticket pequeno de ~$182.
- JitoSOL pode ter vantagem de desenho/fluxo do ecossistema Jito (inclui componente relacionado a MEV/tips), mas para capital pequeno o risco operacional e de liquidez costuma pesar mais.
- Split pequeno em JitoSOL permite testar exposição sem comprometer a operação principal.

**Regra decisória objetiva (simples):**
1. Se houver necessidade de máxima liquidez/integração imediata no mesmo dia: priorizar mSOL (70–100%).
2. Se quiser diversificar ecossistema com risco de execução baixo: 10–15% em JitoSOL apenas.
3. Reequilibrar só após 1 snapshot semanal com métrica de **peg + slippage + APY líquido**.

---

## 1) Entrega: passo a passo (<= 15 min em total)

### 1.1 Preparação (1–2 min)
1. Abra apenas wallets oficiais (Phantom/Backpack/Solflare) no navegador dedicado.
2. Confirme rede **Solana Mainnet**.
3. Separe saldo líquido: valor alvo + **0,15–0,25 SOL** de buffer de taxa.
4. Abra docs/taxas dos protocolos.

### 1.2 Escolher protocolo (30–60s)
- Verifique APY líquido atual (APY bruto − taxa de protocolo − custo de saída estimado).
- Confira disponibilidade da rota de saída no dia (DEX/AMM) no par `mSOL/SOL` e `JitoSOL/SOL`.

**Links oficiais**
- Marinade (mSOL):
  - https://www.marinade.finance/
  - https://app.marinade.finance/
  - https://docs.marinade.finance/
- Jito (JitoSOL):
  - https://www.jito.network/
  - docs.jito.network
  - (se houver) página/portal oficial do produto JitoSOL
- Swap/rebalance confiável:
  - https://jup.ag/
  - https://jupiter.ag/ (frontend alternativo)

### 1.3 UI — mint/swap (8–10 min)

#### Opção A (preferencial): site/protocolo oficial
- **mSOL:** app.marinade.finance → Stake/Mint mSOL.
- **JitoSOL:** página oficial Jito → fluxo de stake/mint correspondente.
- Confirme o resumo da tx antes de aprovar.

#### Opção B (alternativa): Jupiter
- Abrir Jupiter e trocar `SOL -> mSOL` ou `SOL -> JitoSOL`.
- Útil se o app oficial estiver lento/fora de manutenção.

### 1.4 Slippage sugerido e proteção anti-spoof
- **Swap inicial:** 0.30%–0.80% (entrada) dependendo de volatilidade.
- **Saída (desfazer/saída parcial):** 0.50%–1.00% ou menos, evitando picos.
- Ajuste conservador: se quote tiver spread/impacto muito acima disso, aguarde ou reduza o tamanho.

### 1.5 Anti-phishing / spoofed domains (obrigatório)
- **Checklist de domínio:**
  - conferir HTTPS, sem letras parecidas (ex.: `marinade-finance[.]com`, `jito[.]finance` etc.), sem subdomínio estranho.
  - validar que a URL é exatamente um dos links oficiais listados.
- **Checklist de wallet:**
  - recusar pop-ups de extensão pedindo permissões genéricas (“aprovar tudo”, “upgrade authority”, “close account”).
  - conferir programa/token mostrado após assinatura (evitar contrato desconhecido).
- **Checklist de confirmação:**
  - conferir o símbolo/token esperado e slippage final no summary.
  - conferir se a transação só gasta SOL/tokens esperados.

### 1.6 Evidência e contabilidade (5+ pontos)
- Salve tx hash, URL da rota e snapshot de preço no momento.
- Registre:
  - evento de troca (timestamp, protocolo, quantidade SOL antes, quantidade mSOL/JitoSOL após, taxa + slippage)
  - protocolo de custódia e fees da tx.
- Em fluxo de contabilidade (Brasil/US):
  - classifique como **evento de troca/recebimento** (entrada do LST)
  - registre custo/valor de aquisição por lote
  - acompanhe recompensas/juros separadamente para tributação e custo fiscal.

> *Nota fiscal/tributária pode variar por jurisdição; sempre confirmar com contador para o tratamento final de staking rewards e ganho de capital.*

### 1.7 Saída/rollback (2–3 min)
1. Converter apenas parcela de segurança (ex.: 25–50%) primeiro.
2. Fazer swap de volta via o mesmo protocolo ou Jupiter.
3. Validar recebimento de SOL + fees e reavaliar peg antes de fechar tudo.

---

## 2) Alternativa programável (se houver infra)

Se houver automação preparada (sem executar aqui):
1. **Rota de entrada (paper/Sim):** obter quote e construir transação em Jupiter API (`swap`), com route de `SOL` para `mSOL` ou `JitoSOL`.
2. Definir parâmetros conservadores: `slippageBps` baixo e limite de saída por passo.
3. Validar no ambiente de simulação e registrar `tx` sem assinatura.
4. Para saída, repetir inverso (`mSOL/JitoSOL -> SOL`) com split de size.

Observação prática: manter chave privada fora do agente (sem auto-custódia do subagent). Nada de executar em produção neste subtask.

---

## 3) Matriz curta de risco (prioridade)

| Risco | JitoSOL | mSOL | Mitigação recomendada |
|---|---|---|---|
| **Smart-contract** | Moderado | Moderado | Preferir código/auditoria pública, acompanhar incidentes, limitar exposição inicial |
| **Depeg/peg drift** | Baixo/Moderado | Baixo/Moderado | Limite de exposição, monitor diário de `token/SOL`, saída parcial em sinais fracos |
| **Liquidez / slippage** | Variável conforme rota | Geralmente mais denso em TVL e pares grandes | usar slippage guardado, split de saída, não executar em janelas de stress |
| **MEV / validator risk** | Exposição de desenho ligada ao ecossistema Jito | Menor foco em Jito, risco concentrado em conjunto de validadores/operadores | diversificar protocolo e manter LTV conservador |
| **Custodial / aprovação excessiva** | Alto impacto se aprovações amplas | idem | aprovações mínimas, sem revoke/approve amplo, wallets limpas |

**Regra de risco:** se qualquer risco cruzar limiar (peg >1%, slippage alta repetida, app instável), reduzir 100% da nova exposição naquele dia.

---

## 4) Pós-stake (baixo risco) para ~US$182

### 4.1 Ideias conservadoras
1. **Lend básico em vault com LTV baixo (sem loops):** usar parte dos LST como collateral e manter baixa exposição emprestada.
2. **Collateral + buffer de margem alto:** em Drift/Marginfi, usar só percentual pequeno para não gerar margem call em micro-oscilações.
3. **Hold + reinvest manual periódico:** manter 100% do LST sem borrow; retirar apenas parte para rebalance/necessidades.

### 4.2 O que evitar
- Fazer loops de alavancagem (borrow/lend em cadeia).
- Entrar com LTV alto no início.
- Usar tokens recém-mintados como base de múltiplas operações simultâneas sem monitor de preço.

---

## 5) Checklist final (resumo para execução real)

- [ ] 1) Confirmar links oficiais e guardar favoritos antes de conectar wallet.
- [ ] 2) Definir split inicial (ex.: 90% mSOL / 10% JitoSOL) e registrar regra de revisão.
- [ ] 3) Executar entrada por app oficial (ou Jupiter como backup).
- [ ] 4) Ajustar slippage conforme volatilidade e recusar quote ruim.
- [ ] 5) Registrar prova (hash, valor recebido, slippage, taxa).
- [ ] 6) Monitorar 24h de peg + slippage + APY líquido.
- [ ] 7) Só partir para compor DeFi após validação de saída sem estresse.

---

**Status final solicitado:** **plan_submitted**
