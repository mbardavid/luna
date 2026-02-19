# MEMORY.md

## 2026-02-16

- Matheus prefere automações recorrentes para manutenção do OpenClaw (incluindo updates automáticos e rotina diária de log de memória).
- Rotina-base operacional definida com três frentes: healthcheck diário, update automático diário e logging diário de memória.
- Criado o sub-agente "Luan" especializado em codificação (senior developer).
- Luan possui workspace próprio (`workspace-luan`) e identidade focada em "Code First".
- Luan está vinculado ao grupo de Telegram `-5210149200`.

## 2026-02-17

- Ambiente operacional estável com healthcheck diário validando OpenClaw/browser/Gmail readonly.
- Integração Gemini CLI consolidada via configuração persistente no gateway (`GOOGLE_GENAI_USE_GCA=true`).

## 2026-02-18

- Em instalação OpenClaw não-git, a rotina de auto-update pode executar sem mudança de versão; tratar isso como comportamento esperado e reportar claramente.
- Reinício via `SIGUSR1` segue válido após rotina de update, mantendo continuidade operacional.

## 2026-02-19

- Processo diário de memória passou a consolidar também itens acionáveis por taxonomia (Skills, Tools, Workflows/Rotinas, Docs/Processo) para facilitar manutenção contínua.
