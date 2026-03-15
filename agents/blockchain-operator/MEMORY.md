# MEMORY.md — blockchain-operator

Memória curada (apenas aprendizados duráveis; sem log bruto).

## 2026-03 (durável)

- **Dependência externa frágil:** Gemini/Cloud Code Assist pode retornar **403 (ToS/disabled)** e quebrar caminhos de dispatch/automação; manter **fallback explícito** (troca de provider/modelo/rota) documentado e testado.
- **Dependência do browser tooling:** o tool `browser` depende de um binário Chromium-based disponível (Chrome/Chromium/Brave/Edge) **ou** configuração explícita de `browser.executablePath`; sem isso, rotinas de automação/UI falham.
- **Higiene operacional de memória:** workspaces novos precisam de estrutura mínima (`memory/` + logs diários + registry/lessons/active-tasks) para não perder contexto e para permitir rotinas de auditoria.
- **Rastreabilidade multiagente:** usar `sessions_list(24h)` como fonte primária da rotina diária para capturar `kind`, IDs e `transcriptPath` (investigação/forense mais fácil).
- **Triagem de workspace legado sem eventos:** quando as últimas 24h mostram apenas atualização do log diário e `state/audit.jsonl` permanece parado por muitos dias, registrar explicitamente a ambiguidade entre ociosidade real e falha silenciosa de ingestão/dispatch; não inferir saúde operacional apenas pela ausência de atividade.
