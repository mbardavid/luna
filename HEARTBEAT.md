# HEARTBEAT.md

## Mission Control — Task Drain

Quando não houver tasks `in_progress` no MC, puxar a próxima task `inbox` e executar:

1. Consultar MC: `curl -s "http://localhost:8000/api/v1/boards/0b6371a3-ec66-4bcc-abd9-d4fa26fc7d47/tasks" -H "Authorization: Bearer luna_mission_control_access_token_stable_v1_6741ef7ffc207adb58ce632e7ff1d9913dbf2e9c44441aac"`
2. Filtrar: se existir `in_progress` → não fazer nada (já tem trabalho rodando)
3. Se zero `in_progress` e existir `inbox`:
   - Pegar a task inbox mais antiga (ou a de maior prioridade/parent relevante)
   - Mover para `in_progress` no MC
   - Spawnar o Luan (ou agente correto) com a task
   - Monitorar e atualizar MC conforme progresso
4. Se zero `in_progress` e zero `inbox` → HEARTBEAT_OK

## Prioridade de execução
- Tasks filhas do "Polymarket Market Maker" têm prioridade (são sequenciais: fase N depende de N-1)
- Tasks órfãs/independentes podem ser executadas em paralelo se não conflitarem

## Regras
- Sempre atualizar MC antes e depois de spawnar (status + session_key)
- Se subagent falhar → reagir imediatamente (investigar, re-spawnar, notificar)
- Se gateway precisar reiniciar → notificar Matheus, reiniciar, re-spawnar
- Nunca deixar task `in_progress` sem subagent ativo associado
