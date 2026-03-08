# Capital Timeline Reconciliation — 2026-03-08

## Verdict
- Janela PMM-era confiavel: **2026-02-28T22:43:47.779Z** -> **2026-03-08T02:36:48.881Z**.
- Stack total caiu de **$408.15** para **$229.03** (-179.12).
- Polygon caiu de **$233.32** para **$58.78** (-174.54).
- Solana caiu de **$174.76** para **$170.19** (-4.57).
- Nessa janela, **97.4%** da queda total veio de Polygon e **2.6%** veio de Solana.

## PMM Realized PnL
- prod-001: 2.1976 USD
- prod-002: 0.0000 USD
- prod-003: 0.0000 USD
- prod-004: -45.9276 USD
- prod-005: -0.0875 USD

Piores decisions:
- quant-20260306T230134Z: -132.5627 USD
- quant-20260307T165353Z: -0.0875 USD
- legacy_no_decision_id: 2.1976 USD
- quant-20260306T033353Z: 36.4534 USD
- quant-20260306T142026Z: 50.1817 USD

Piores mercados:
- unknown_market: -43.8174 USD

## Baseline selection
- Snapshots com polygon quase zerada em 2026-03-02 coexistem com snapshots completos minutos depois; foram tratados como leituras parciais/incompletas para fins de baseline PMM.
- A comparacao PMM-era usa o primeiro snapshot com polygon >= 100 USD e total >= 300 USD como baseline operacional confiavel.

## Meaningful Snapshots
- 2026-02-18T04:25:27.621Z: total=438.82 polygon=0.00 solana=438.82 arbitrum=0.00
- 2026-02-18T19:37:19.830Z: total=415.01 polygon=0.00 solana=415.01 arbitrum=0.00
- 2026-02-19T00:17:30.170Z: total=419.14 polygon=0.00 solana=419.14 arbitrum=0.00
- 2026-02-19T02:36:03.708Z: total=415.82 polygon=0.00 solana=415.82 arbitrum=0.00
- 2026-02-19T06:18:04.437Z: total=418.88 polygon=0.00 solana=418.88 arbitrum=0.00
- 2026-02-19T09:50:09.490Z: total=414.51 polygon=0.00 solana=414.51 arbitrum=0.00
- 2026-02-19T13:08:06.901Z: total=410.60 polygon=0.00 solana=410.60 arbitrum=0.00
- 2026-02-19T19:14:48.520Z: total=416.79 polygon=0.00 solana=416.79 arbitrum=0.00
- 2026-02-21T01:46:14.696Z: total=411.99 polygon=0.00 solana=361.99 arbitrum=0.00
- 2026-02-21T20:16:42.202Z: total=412.55 polygon=0.00 solana=182.55 arbitrum=0.00
- 2026-02-25T05:01:27.479Z: total=405.01 polygon=0.00 solana=175.01 arbitrum=0.00
- 2026-02-25T14:25:26.903Z: total=409.24 polygon=0.00 solana=179.24 arbitrum=0.00
- 2026-02-25T15:00:29.629Z: total=182.44 polygon=0.00 solana=182.44 arbitrum=0.00
- 2026-02-25T15:21:40.539Z: total=412.96 polygon=0.00 solana=181.13 arbitrum=231.83
- 2026-02-25T16:27:00.398Z: total=417.64 polygon=0.00 solana=185.76 arbitrum=231.87
- 2026-02-28T21:01:35.672Z: total=175.03 polygon=1.10 solana=173.86 arbitrum=0.06
- 2026-02-28T22:43:47.779Z: total=408.15 polygon=233.32 solana=174.76 arbitrum=0.06
- 2026-02-28T23:10:43.537Z: total=347.60 polygon=174.07 solana=173.47 arbitrum=0.06
- 2026-02-28T23:41:31.893Z: total=405.97 polygon=233.56 solana=172.34 arbitrum=0.06
- 2026-03-01T04:21:14.055Z: total=409.74 polygon=230.77 solana=178.91 arbitrum=0.06
- 2026-03-01T17:07:12.018Z: total=400.36 polygon=227.67 solana=172.63 arbitrum=0.06
- 2026-03-01T20:39:24.591Z: total=397.28 polygon=229.18 solana=168.03 arbitrum=0.06
- 2026-03-02T00:09:04.396Z: total=400.61 polygon=229.07 solana=171.47 arbitrum=0.06
- 2026-03-02T03:15:55.918Z: total=178.50 polygon=5.08 solana=173.36 arbitrum=0.06
- 2026-03-02T03:31:57.788Z: total=404.42 polygon=231.00 solana=173.36 arbitrum=0.06
- 2026-03-02T12:23:40.500Z: total=400.32 polygon=228.39 solana=171.87 arbitrum=0.06
- 2026-03-02T14:53:03.271Z: total=176.91 polygon=1.57 solana=175.29 arbitrum=0.06
- 2026-03-02T15:47:03.184Z: total=183.50 polygon=1.57 solana=181.86 arbitrum=0.06
- 2026-03-02T16:33:22.675Z: total=410.89 polygon=228.52 solana=182.30 arbitrum=0.06
- 2026-03-02T19:15:12.745Z: total=403.26 polygon=224.33 solana=178.87 arbitrum=0.06
- 2026-03-03T01:32:16.040Z: total=393.25 polygon=216.22 solana=176.97 arbitrum=0.06
- 2026-03-08T02:03:08.388Z: total=229.03 polygon=58.78 solana=170.19 arbitrum=0.06
- 2026-03-08T02:36:48.881Z: total=229.03 polygon=58.78 solana=170.19 arbitrum=0.06
