# Mission Control — Fase 1: Runbook de Validação

**Objetivo:** Confirmar que todos os componentes da Fase 1 funcionam end-to-end.  
**Pré-requisitos:** MC rodando (`curl http://localhost:8000/health` → `{"ok":true}`), custom fields criados, crons ativos.

---

## Checklist de Validação

### 1. Infraestrutura Base
```bash
# MC health
curl -s http://localhost:8000/health
# Esperado: {"ok":true}

# Dashboard acessível
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000
# Esperado: 200

# Custom fields existem
TOKEN="luna_mission_control_access_token_stable_v1_6741ef7ffc207adb58ce632e7ff1d9913dbf2e9c44441aac"
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/organizations/me/custom-fields | python3 -c "import sys,json; fields=[f['field_key'] for f in json.load(sys.stdin)]; expected={'mc_session_key','mc_retry_count','mc_progress','mc_delivered','mc_last_error'}; missing=expected-set(fields); print('OK' if not missing else f'MISSING: {missing}')"
# Esperado: OK
```

### 2. Criar Task via mc-client
```bash
cd /home/openclaw/.openclaw/workspace

# Criar task
./scripts/mc-client.sh create-task "Phase1 Validation Test" "Teste end-to-end do runbook" --priority high --assignee luna
# Esperado: JSON com task_id

# Listar tasks e confirmar
./scripts/mc-client.sh list-tasks | python3 -c "import sys,json; tasks=json.load(sys.stdin).get('items',[]); [print(f'{t[\"id\"][:8]}.. {t[\"status\"]:12} {t[\"title\"]}') for t in tasks[:5]]"
```

### 3. Spawn Wrapper (task + sessão linkadas)
```bash
# Criar task e spawnar sub-agente via wrapper
./scripts/mc-spawn-wrapper.sh --json \
  --title "Validation: spawn test" \
  --description "Crie um arquivo /tmp/mc-validation-test.txt com conteúdo 'ok'" \
  --assignee luna \
  --agent luan

# Esperado: JSON com taskId + sessionKey preenchidos
# Verificar no MC:
TASK_ID="<id do output acima>"
./scripts/mc-client.sh get-task "$TASK_ID" | python3 -c "import sys,json; t=json.load(sys.stdin); print(f'session_key={t.get(\"custom_field_values\",{}).get(\"mc_session_key\")}')"
# Esperado: session_key=<algo não-vazio>
```

### 4. Simular Sessão Morta → Watchdog Recovery
```bash
# Criar task com sessionKey fake (simula sessão que morreu)
TASK_ID=$(./scripts/mc-client.sh create-task "Watchdog Test" "Sessão morta" --priority medium --assignee luna | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

# Linkar sessionKey inexistente
./scripts/mc-link-task-session.sh "$TASK_ID" "__fake_dead_session__"

# Atualizar pra in_progress
./scripts/mc-client.sh update-task "$TASK_ID" --status in_progress

# Rodar watchdog manualmente
./scripts/mc-watchdog.sh --verbose
# Esperado: stats com "recovered": 1 (primeira tentativa)

# Rodar de novo (simula segundo ciclo)
./scripts/mc-watchdog.sh --verbose
# Esperado: stats com "recovered": 1 (segunda tentativa, retry_count=2)

# Rodar terceira vez
./scripts/mc-watchdog.sh --verbose
# Esperado: stats com "moved_to_needs_approval": 1 (max retries atingido)

# Verificar no MC
./scripts/mc-client.sh get-task "$TASK_ID" | python3 -c "import sys,json; t=json.load(sys.stdin); f=t.get('custom_field_values',{}); print(f'status={t[\"status\"]} retries={f.get(\"mc_retry_count\")} error={f.get(\"mc_last_error\")}')"
# Esperado: status=review retries=2 error=needs_approval
```

### 5. Delivery de Resultado
```bash
# Criar task done sem delivery
TASK_ID=$(./scripts/mc-client.sh create-task "Delivery Test" "Resultado de teste" --priority medium --assignee luna | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
./scripts/mc-client.sh update-task "$TASK_ID" --status done --comment "Resultado final: arquivo criado com sucesso em /tmp/test.txt"

# Rodar delivery (dry-run primeiro)
./scripts/mc-delivery.sh --verbose --dry-run
# Esperado: scanned=1, dry_run=true, mostra mensagem que seria enviada

# Rodar delivery real
./scripts/mc-delivery.sh --verbose
# Esperado: delivered=1, mensagem aparece no #general-luna com o resultado

# Verificar flag
./scripts/mc-client.sh get-task "$TASK_ID" | python3 -c "import sys,json; t=json.load(sys.stdin); print(f'delivered={t.get(\"custom_field_values\",{}).get(\"mc_delivered\")}')"
# Esperado: delivered=True

# Rodar delivery de novo (idempotência)
./scripts/mc-delivery.sh --verbose
# Esperado: scanned=0 (não re-entrega)
```

### 6. Stall Detection (sem re-spam)
```bash
# Criar task in_progress com sessão ativa mas sem atividade
TASK_ID=$(./scripts/mc-client.sh create-task "Stall Test" "Teste stall" --priority medium --assignee luna | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
./scripts/mc-link-task-session.sh "$TASK_ID" "__stall_test_session__"
./scripts/mc-client.sh update-task "$TASK_ID" --status in_progress

# Rodar watchdog com threshold baixo (1 min) pra testar
./scripts/mc-watchdog.sh --verbose --stalled-minutes 0
# Esperado: stalled=1, mc_last_error=stalled

# Rodar de novo — NÃO deve re-comentar
./scripts/mc-watchdog.sh --verbose --stalled-minutes 0
# Esperado: stalled=0 (já estava marcado, não re-marca)
```

### 7. Resource Monitor (Degrade Mode)
```bash
# Rodar resource monitor
./scripts/mc-resource-monitor.sh --verbose
# Esperado: status normal se RAM < 80%

# Verificar que kill está desativado por default
grep "RESOURCE_MONITOR_KILL" ./scripts/mc-phase1-cron.sh
# Esperado: MC_RESOURCE_MONITOR_KILL:-0
```

### 8. Gateway Restart Recovery
```bash
# Criar task in_progress real
TASK_ID=$(./scripts/mc-client.sh create-task "Restart Test" "Teste recovery pós-restart" --priority medium --assignee luna | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
./scripts/mc-client.sh update-task "$TASK_ID" --status in_progress

# Restart gateway
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus
systemctl --user restart openclaw-gateway.service
sleep 10

# Verificar notificação de restart no Discord
# Esperado: mensagem "🔄 Gateway reiniciado e online"

# Rodar watchdog (simula primeiro heartbeat pós-restart)
./scripts/mc-watchdog.sh --verbose --startup-recovery
# Esperado: detecta task órfã e tenta recovery
```

---

## Critérios de Aceite — Fase 1 Concluída

- [ ] MC healthy + dashboard acessível
- [ ] Custom fields criados e funcionais
- [ ] mc-client.sh CRUD funciona
- [ ] Spawn wrapper cria task + linka sessionKey
- [ ] Watchdog detecta sessão morta e faz retry (max 2)
- [ ] Watchdog marca stalled sem re-spam
- [ ] Delivery entrega resultado real (com comment) no Discord
- [ ] Delivery é idempotente (não re-entrega)
- [ ] Resource monitor roda sem kill por default
- [ ] Gateway restart → notificação + recovery
- [ ] Crons mc-watchdog, mc-delivery, mc-resource-monitor rodando a cada 15min

---

## Limpeza Pós-Validação

```bash
# Deletar tasks de teste
./scripts/mc-client.sh list-tasks | python3 -c "
import sys,json
tasks = json.load(sys.stdin).get('items',[])
for t in tasks:
  title = t.get('title','')
  if 'QA' in title or 'Test' in title or 'Smoke' in title or title in ('P','P2','P3','P4','P5','P6','P7'):
    print(t['id'], title)
"
# Revisar lista, depois deletar:
# ./scripts/mc-client.sh delete-task <task_id>
```
