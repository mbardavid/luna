# Hyperliquid Multi-Route Pipelines (v1)

Resumo da expansão de rotas/pipelines e seus limites de confiança.

## 1) Solana -> HL via deBridge

- **Status no runtime:** `NOT_SUPPORTED` (explícito)
- **Motivo técnico:** deBridge entrega para `dstChainTokenOutRecipient` em chain de destino; não executa fluxo Bridge2 nativo do Hyperliquid.
- **Pipeline recomendado:**
  1. `bridge` Solana -> Arbitrum (deBridge)
  2. `hyperliquid.bridge.deposit` Arbitrum -> HL (Bridge2)

## 2) Base -> HL via deBridge

- **Status no runtime:** `NOT_SUPPORTED` (explícito)
- **Motivo técnico:** mesmo limite acima (deBridge não chama `withdraw3`/Bridge2 do HL).
- **Pipeline recomendado:**
  1. `bridge` Base -> Arbitrum (deBridge)
  2. `hyperliquid.bridge.deposit` Arbitrum -> HL (Bridge2)

## 3) Arbitrum -> HL via native platform deposit

- **Status no runtime:** **SUPORTADO**
- **Implementação:** transferência de **USDC nativo Arbitrum** para contrato Bridge2
  - `HYPERLIQUID_BRIDGE2_ADDRESS` default: `0x2df1c51e09aecf9cacb7bc98cb1742757f163df7`
  - mínimo default: `5 USDC` (configurável)
- **Guardrail crítico:** endereço do remetente Arbitrum deve bater com `HYPERLIQUID_ACCOUNT_ADDRESS`.

## 4) HL -> Arbitrum via native platform withdraw

- **Status no runtime:** **SUPORTADO**
- **Implementação:** ação Hyperliquid `withdraw3` (via SDK) para recipient EVM em Arbitrum.
- **Guardrails:**
  - asset fixo `USDC`
  - recipient explícito e allowlisted
  - preflight de saldo spot USDC quando `HYPERLIQUID_ACCOUNT_ADDRESS` está disponível

## 5) HL -> Base via deBridge

- **Status no runtime:** `NOT_SUPPORTED` (direto HL via deBridge)
- **Pipeline recomendado:**
  1. `hyperliquid.bridge.withdraw` HL -> Arbitrum
  2. `bridge` Arbitrum -> Base (deBridge)

## 6) HL -> Solana via deBridge

- **Status no runtime:** `NOT_SUPPORTED` (direto HL via deBridge)
- **Pipeline recomendado:**
  1. `hyperliquid.bridge.withdraw` HL -> Arbitrum
  2. `bridge` Arbitrum -> Solana (deBridge)

---

## Trust boundaries (resumo)

- **Control/Execution plane:** valida schema/intent/policy antes de connector live.
- **Policy boundary:** allowlists (chains/assets/recipients/rotas), simulation gate, limits, key segregation.
- **deBridge boundary:** quote/build/submit do tx de origem + tracking básico de ordem; sem integração nativa com Bridge2 HL.
- **Arbitrum boundary:** execução on-chain ERC20 (USDC) para Bridge2.
- **Hyperliquid boundary:** assinatura API wallet para ações L1 (`usdClassTransfer`, `withdraw3`, ordens).
- **Operational boundary:** idempotência + breaker + audit log append-only.

---

## Execução validada (canário real) — 2026-02-20

Objetivo: validar pipeline multi-step completo com teto de custo <= 10 USDC (origem Solana).

### Pré-condições que se provaram necessárias

1. **Identidade única EVM/HL**
   - `HYPERLIQUID_ACCOUNT_ADDRESS` deve casar com signer EVM usado em Arbitrum.
   - `HYPERLIQUID_API_WALLET_PRIVATE_KEY` deve assinar para a mesma conta HL operacional.

2. **Gas em Arbitrum**
   - Necessário ETH para tx do depósito Bridge2.
   - Pipeline recomendado: bridge de pequena fração de USDC para ETH em Arbitrum antes do depósito nativo.

3. **Allowlist de recipient**
   - Recipient EVM operacional precisa estar em `allowlists.recipients` para bridges Solana/Base -> Arbitrum.

### Sequência executada (live)

1. Bridge `SOLANA USDC -> ARBITRUM USDC` para EVM operacional.
2. Bridge `SOLANA USDC -> ARBITRUM ETH` para financiar gas.
3. `deposit` nativo `ARBITRUM -> HYPERLIQUID` via Bridge2 (USDC).
4. `withdraw` nativo `HYPERLIQUID -> ARBITRUM` via `withdraw3`.

### Comportamentos observados

- `withdraw3` com **1 USDC** falha por limite/fee mínima de saque (`Withdrawal is smaller than fee`).
- `withdraw3` com **2 USDC** retornou `status: ok` no endpoint HL.
- Confirmação final deve considerar latência de liquidação/credito no destino.

### Playbook reutilizável (resumido)

- **Step A:** garantir identidade HL == signer EVM.
- **Step B:** garantir recipient allowlisted.
- **Step C:** bridge USDC -> Arbitrum (principal do teste).
- **Step D:** bridge pequena parcela USDC -> ETH (gas budget).
- **Step E:** deposit Bridge2 (>= mínimo da plataforma).
- **Step F:** withdraw3 com valor acima da taxa mínima.
- **Step G:** reconciliar saldos on-chain + estado HL antes de nova iteração.
