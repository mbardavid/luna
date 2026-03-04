# MEMORY.md — blockchain-operator

Memória curada (apenas aprendizados duráveis; sem log bruto).

## 2026-03 (durável)

- **Dependência externa frágil:** Gemini/Cloud Code Assist pode retornar **403 (ToS/disabled)** e quebrar caminhos de dispatch/automação; manter **fallback explícito** (troca de provider/modelo/rota) documentado e testado.
- **Higiene operacional de memória:** workspaces novos precisam de estrutura mínima (`memory/` + logs diários + registry/lessons/active-tasks) para não perder contexto e para permitir rotinas de auditoria.
