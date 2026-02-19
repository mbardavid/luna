# Pesquisa técnica prática (2026-02-17)

## 1) Solana — CLI/SDK/clients

### Opções reais

1. **Solana CLI (Agave / `solana`)**
   - Referência oficial: `docs.anza.xyz/cli/usage`
   - Pontos fortes:
     - Excelente para operações administrativas e validação manual (`balance`, `transfer`, `confirm`, `program`)
     - Útil como fallback operacional e troubleshooting
   - Limite:
     - Menos ergonômico para orquestração programática complexa

2. **`@solana/web3.js` (v1.x, manutenção) + migração futura para `@solana/kit`**
   - Fonte: repositório `solana-foundation/solana-web3.js` indica manutenção de v1.x e sucessor `@solana/kit`
   - Pontos fortes:
     - Ecossistema maduro, ampla base de exemplos
     - Bom para transferências, assinatura, interação com programas
   - Limite:
     - v1.x em manutenção; roadmap deve contemplar `@solana/kit`

3. **Cookbook + RPC direto para receitas operacionais**
   - Fonte: Solana Cookbook
   - Uso:
     - padrões para envio de SOL/tokens, fees e prioridade

### Escolha para MVP

- **Runtime principal:** `@solana/web3.js` (rápido para entregar)
- **Fallback operacional:** CLI `solana`
- **Plano futuro:** trilha de migração gradual para `@solana/kit`

---

## 2) Hyperliquid — integração programática

### Opções reais

1. **API oficial (`/info` e `/exchange`)**
   - Fonte oficial: Hyperliquid Docs (API)
   - Cobertura:
     - market data, estado de usuário, ordens, cancelamentos, transferência, etc.

2. **SDK Python oficial (`hyperliquid-python-sdk`)**
   - Fonte oficial: `hyperliquid-dex/hyperliquid-python-sdk`
   - Pontos fortes:
     - Assinatura de payloads e exemplos atualizados pelo time
   - Limite:
     - stack principal do agente pode ser Node, exigindo ponte ou conector híbrido

3. **SDKs TypeScript comunitários + CCXT**
   - listados na doc oficial como comunitários
   - boa opção para prototipação rápida, com maior risco de drift vs. API oficial

### Observações de segurança críticas (oficiais)

- Nonce é central para anti-replay e funciona no modelo próprio da HL.
- API wallets/agent wallets podem ser podadas; recomendação oficial: **não reutilizar endereço de API wallet após pruning**.

### Escolha para MVP

- **Curto prazo:** API oficial + dry-run/info já pronto
- **Execução live recomendada:** integrar SDK Python oficial para assinatura robusta
- **Modelo de chaves:** API wallet separada por processo/subaccount para evitar colisão de nonce

---

## 3) Cross-chain — deBridge (DLN) + alternativas

### deBridge (escolha primária)

- Docs mostram endpoint `create-tx` como fluxo unificado de **quote + tx build**.
- Endpoints e guias:
  - Quickstart + `api-integrator-example`
  - `dln/order/create-tx`
  - same-chain swaps (`/v1.0/chain/estimation`, `/v1.0/chain/transaction`)
- Pontos fortes:
  - suporte EVM + Solana
  - modelo de execução orientado a viabilidade de preenchimento
  - integração com payload pronto para assinatura

### CLI/MCP

- Não há evidência de CLI oficial madura e dominante para produção (no material público consumido).
- Integração prática é API-first (REST + SDK examples).

### Alternativas confiáveis

- **LI.FI**: forte agregação multi-chain (inclui Solana/EVM), boa alternativa de roteamento
- **Socket**: opção adicional para rotas/infra cross-chain

### Escolha para MVP

- **Primário:** deBridge API (quote/build)
- **Fallback planejado:** LI.FI para contingência de rotas

---

## 4) Wallet tooling — Phantom/MetaMask em contexto de automação hot wallet

### Fato técnico

- MetaMask e Phantom docs focam em API de provider para dApps com **aprovação do usuário** (UI wallet prompt).
- Isso não é ideal para executor totalmente autônomo headless.

### Estratégia recomendada para execução autônoma

1. Criar contas operacionais dedicadas (uma por domínio de risco).
2. Exportar chaves dessas contas de forma controlada e armazenar no host com proteção (env/KMS/sealed secret).
3. Assinar server-side com libs nativas (`viem`/`@solana/web3.js`), mantendo Phantom/MetaMask como interface humana de supervisão.

### Escolha para MVP

- **Base/EVM:** `viem` com private key local segregada
- **Solana:** `@solana/web3.js` + keypair dedicada
- **Hyperliquid:** API wallet dedicada (separada da master wallet)

---

## Referências usadas

- Solana CLI: `https://docs.anza.xyz/cli/usage`
- Solana JS SDK repo: `https://github.com/solana-foundation/solana-web3.js`
- Solana Cookbook: `https://solana.com/developers/cookbook`
- Hyperliquid API: `https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api`
- Hyperliquid exchange/info/nonces: páginas da seção API docs
- Hyperliquid SDK Python: `https://github.com/hyperliquid-dex/hyperliquid-python-sdk`
- deBridge docs: `https://docs.debridge.com/`
- deBridge create-tx e guias: seção DLN integration guidelines + API reference
- Base network connectivity: `https://docs.base.org/base-chain/quickstart/connecting-to-base`
- MetaMask Wallet API: `https://docs.metamask.io/wallet/`
- Phantom docs: `https://docs.phantom.com/`
