# Validation Report — 2026-02-18

## 1) Testes automatizados

Comando:

```bash
npm test
```

Resultado:

- `33/33` testes pass
- evidência: `artifacts/npm-test-2026-02-18.log`

## 2) Dry-run de todos os flows A2A v1

Comando:

```bash
npm run dry-run:a2a
```

Resultado:

- `12/12` operações do schema v1 executadas em dry-run com sucesso
- evidências:
  - resumo estruturado: `artifacts/dry-run-a2a-v1-results.json`
  - log de execução: `artifacts/dry-run-a2a-v1.log`

Operações cobertas:

- bridge
- swap.jupiter
- swap.raydium
- swap.pumpfun
- defi.deposit
- defi.withdraw
- hyperliquid.spot.order
- hyperliquid.perp.order
- transfer
- send
- hyperliquid.cancel
- hyperliquid.modify

## 3) Segurança de dependências

Comando:

```bash
npm audit --omit=dev
```

Resultado:

- 0 vulnerabilities
- evidência: `artifacts/npm-audit-2026-02-18.log`

## 4) Revisão de segurança

Relatório:

- `audit/security-review-2026-02-18.md`
