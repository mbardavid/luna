# PMM + Quant Pre-Live Check

## Objetivo

Garantir que o próximo live rode com:
- `public CLOB direct`
- `private CLOB proxy-only`
- wallet flat ou só com poeira operacional
- Quant gated por `free_collateral` e inventário recuperável
- attribution report mostrando se o edge veio de MM ou direção

## Checklist

1. Wallet state

```bash
cd /home/openclaw/.openclaw/workspace/polymarket-mm
python3 scripts/flatten_positions.py --json
```

Critério:
- `recoverable_inventory_usdc = 0`
- `unrecoverable_without_new_trade = []`
- apenas `dust_positions` residuais
- `free_collateral_usdc` alinhado com o capital live aprovado pelo Quant

2. Transporte

```bash
cd /home/openclaw/.openclaw/workspace-quant-strategist
python3 scripts/decision_engine.py --output /tmp/decision-canary.json --json
```

Critério:
- `metadata.transport_probe_summary.direct.ok_samples > 0`
- `metadata.selected_transport = direct`
- `metadata.wallet_state.available = true`

3. Proxy post canary

Critério:
- ordem autenticada enviada via proxy com fill ou reject sem geoblock
- nenhuma tentativa de `private_post_direct`
- evidência persistida em log/artefato

4. Merge canary

Critério:
- split de `1 USDC` executado
- merge de volta executado
- tx hashes persistidos
- sem `nonce too low`
- sem veto por `max_gas_price_gwei`

5. Quant gating

```bash
cd /home/openclaw/.openclaw/workspace
scripts/quant-cycle.sh --json
scripts/quant-diagnose.sh --json
```

Critério:
- `metadata.wallet_state.live_blockers = []`
- `trading_state = active` apenas se houver mercado rewards com EV líquido positivo
- se `trading_state = standby`, o motivo precisa ser explícito e econômico/operacional

6. Attribution

```bash
cd /home/openclaw/.openclaw/workspace-quant-strategist
python3 scripts/performance-analyzer.py --run-id prod-004 --json
```

Critério:
- `post_trade_diagnosis.pnl_attribution` presente
- `dominant_source` não pode ser tratado como `spread_capture` quando o ganho veio de inventário/carry

## Estado esperado antes do próximo live

- `free_collateral_usdc ~= 232.03`
- wallet economicamente flat
- residual dust `< $1`
- Quant capaz de gerar envelope sem confundir inventory com capital livre
- PMM incapaz de postar privado sem proxy por desenho
