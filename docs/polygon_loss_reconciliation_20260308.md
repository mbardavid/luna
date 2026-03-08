# Polygon Loss Reconciliation — 2026-03-08

## Verdict
- Referencia apos flatten manual: **232.0304 USDC livres** em 2026-03-06 01:46 UTC.
- Estado atual do PMM wallet: **56.7522 USDC** de equity, dos quais **56.2184** livres.
- Queda economica desde o flatten manual: **-175.2782 USD**.

## Principal finding
- O campo `pnl_this_trade` do ledger nao explica a destruicao economica total do wallet.
- A medida economica correta e `wallet_after.total_equity` ao longo dos fills.

## Decomposicao por decision_id
- quant-20260306T230134Z | mercado: Will gas hit (High) $3.75 by March 31? | fills=93 | cashflow=-182.8679 | ledger_pnl=-132.5627 | equity 208.2116->60.8070 (-147.4046)
- quant-20260306T033353Z | mercado: Will Donald Trump post 180-199 Truth Social posts from February 27 to March 6, 2026? | fills=50 | cashflow=-156.1485 | ledger_pnl=36.4534 | equity 227.0361->217.8571 (-9.1790)
- quant-20260306T142026Z | mercado: Will there be no new Supreme Leader of Iran by June 30? | fills=33 | cashflow=-97.4153 | ledger_pnl=50.1817 | equity 217.8841->215.8580 (-2.0261)
- quant-20260307T165353Z | mercado: Will Iran name a successor to Khamenei by March 7? | fills=6 | cashflow=-0.7750 | ledger_pnl=-0.0875 | equity 56.4609->56.3884 (-0.0725)

## Interpretation
- O bloco mais destrutivo foi `quant-20260306T230134Z` no mercado de gas.
- Nesse bloco, o ledger marca `-132.56 USD`, mas a equity do wallet cai **-147.4046 USD** entre o primeiro e o ultimo fill do bloco.
- No agregado dos fills gravados apos o flatten manual, a equity cai **-170.6477 USD** entre o primeiro e o ultimo fill registrados.
- Portanto, a perda economica em Polygon foi real e ocorreu majoritariamente dentro da operacao do PMM; o ledger de PnL atual subestima materialmente a perda total.

## Residual inventory today
- recoverable_inventory_usdc=0.1550
- dust_inventory_usdc=0.3788
- O inventario residual hoje nao explica a queda historica; ele e pequeno demais.

## Worst ledger trades
- 2026-03-07T02:30:39.371521+00:00 | Will gas hit (High) $3.75 by March 31? | SELL YES 20.0 @ 0.47 | pnl=-8.3000 | equity=186.6116
- 2026-03-07T02:33:37.590878+00:00 | Will gas hit (High) $3.75 by March 31? | SELL YES 20.0 @ 0.49 | pnl=-7.9000 | equity=186.0116
- 2026-03-07T02:26:33.265515+00:00 | Will gas hit (High) $3.75 by March 31? | SELL YES 20.0 @ 0.58 | pnl=-6.1000 | equity=191.4116
- 2026-03-07T03:16:20.378025+00:00 | Will gas hit (High) $3.75 by March 31? | SELL NO 20.0 @ 0.19 | pnl=-5.7000 | equity=102.3388
- 2026-03-07T04:01:12.362256+00:00 | Will gas hit (High) $3.75 by March 31? | SELL NO 20.0 @ 0.09 | pnl=-5.6900 | equity=67.4070
- 2026-03-07T02:41:38.672632+00:00 | Will gas hit (High) $3.75 by March 31? | SELL YES 20.0 @ 0.53 | pnl=-5.3500 | equity=176.4475
- 2026-03-07T03:04:33.444498+00:00 | Will gas hit (High) $3.75 by March 31? | SELL NO 20.0 @ 0.19 | pnl=-5.3000 | equity=128.2580
- 2026-03-07T03:15:21.232119+00:00 | Will gas hit (High) $3.75 by March 31? | SELL NO 18.19 @ 0.2 | pnl=-5.0023 | equity=108.5388
- 2026-03-07T02:44:39.324293+00:00 | Will gas hit (High) $3.75 by March 31? | SELL YES 20.0 @ 0.45 | pnl=-4.4549 | equity=168.7547
- 2026-03-07T02:42:21.226312+00:00 | Will gas hit (High) $3.75 by March 31? | SELL NO 20.0 @ 0.29 | pnl=-4.4000 | equity=178.3547
