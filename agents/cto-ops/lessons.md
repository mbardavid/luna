# Lessons — CTO-ops

## Lições aprendidas

_Inicializado em 2026-03-01._

### Padrões de falha de gateway
- Gateway pode se matar via `exec` — sempre usar sentinel para monitorar
- OOM por retry storms — implementar rate limiting em todos os loops
- Processos orphans segurando portas — limpar antes de reiniciar

### Operacionais
- (populado conforme operação)

### Decisões de design revertidas
- (nenhuma ainda)

### Alertas falsos-positivos recorrentes
- (nenhum ainda)
