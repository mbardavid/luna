# Workflow Registry — CTO-ops

## Registro de fluxos
- 2026-03-01: Ativado monitoramento contínuo de gateway/sessões com scripts de sentinel e cron.
- 2026-03-01: Publicado risk policy (`config/cto-risk-policy.json`) com limites anti-spam e kill-switch.

## Processos
- Verificação de recursos, gateway e sessão em base periódica.
- Em risco alto: encaminhar `needs_approval` para trilha de revisão em vez de ação automática.
