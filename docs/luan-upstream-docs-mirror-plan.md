# Plano — Visibilidade de Docs do Workspace Principal para Luan (Sem implementação)

**Task (MC):** fe787e5f-6c90-470c-a063-f9a17afbaffe  
**Workspace:** `/home/openclaw/.openclaw/workspace-luan`  
**Objetivo:** permitir que o Luan (workspace-luan) acesse documentação/arquitetura do workspace principal para desenvolvimento arquitetural, sem risco de sobrescrever ou causar conflitos.

## 1) Escolha recomendada

**Recomendado: Opção 3 (Hybrid) — mirror docs + allowlist (lessons/config) + proposals/MC tasks.**

### Por que não “mirror only”
- Facilita visibilidade, porém permite cópia extensa e potencialmente sensível (configs, segredos, arquivos de estado temporário).
- Cresce risco de drift: Luan pode editar por engano sem rastreio de origem.

### Por que não “shared workspace”
- Alto risco de conflito de escrita, principalmente em `config/*` e arquivos de estado.
- Alterações de Luan e Main em paralelo podem gerar inconsistência de arquitetura e comportamento.

### Vantagem do híbrido
- Dá visibilidade suficiente para raciocínio arquitetural.
- Mantém um **caminho de governança único** para mudanças reais no topo via MC/proposta.
- Facilita rollback e auditoria sem bloquear produtividade local do Luan.

## 2) Estrutura-alvo de mirror

- **Destino local (somente leitura):** `/home/openclaw/.openclaw/workspace-luan/upstream-docs`
- **Fonte principal:** `/home/openclaw/.openclaw/workspace-main`
- **Método de atualização:** `rsync` incremental (preservando metadados de leitura, sem deletar acidentalmente sem confirmação)
- **Modo de atualização:** execução em tarefa/ciclo controlado (cron + state check), nunca contínuo por watcher crítico.

## 3) Allowlist e exclusões

### 3.1 Paths permitidos (`--include` / `--filter`)

- `docs/**`
- `memory/lessons.md`
- `memory/lessons/*.md` *(se houver)*
- `memory/` — **somente** padrões: `*.md` e `workflow-registry.md`
- `analysis-clawsuite.md`
- `config/architecture/**`
- `config/protocols/**`
- `config/ops/**`
- `lessons/**`
- `docs/architecture/**`
- `README.md` no nível raiz

### 3.2 Exclusões obrigatórias (sem risco)

- `.git/**`
- `.github/**`
- `workspace*/**` (qualquer sub-workspace)
- `**/node_modules/**`
- `**/.venv/**`
- `**/target/**`, `**/dist/**`, `**/build/**`
- `**/*.env*`, `**/secrets/**`, `**/credentials/**`, `**/token*`, `**/*.key`, `**/*.pem`, `**/*.p12`
- `**/sessions/**`, `**/*.jsonl`, `**/*.db`, `**/*.sqlite*`
- `**/.turbo/**`, `**/.cache/**`
- `**/openclaw.json` e quaisquer arquivos de runtime de gateway
- `**/*tmp*`, `**/*.swp`

## 4) Mecanismo de sync (rsync incremental)

### 4.1 Script recomendado (ex.: `scripts/luan-upstream-docs-sync.sh`)

- Usar `rsync -a --delete --delete-excluded --itemize-changes`
- Rodar com `--exclude-from` e `--include-from` gerados a partir da allowlist
- Sempre gerar `manifest.json` com hash SHA-256 dos arquivos sincronizados para trilha de auditoria.

Exemplo lógico do ciclo:

1. Resolver `SRC=/home/openclaw/.openclaw/workspace-main`.
2. Resolver `DST=/home/openclaw/.openclaw/workspace-luan/upstream-docs`.
3. Validar `ANTI_LOOP_MARKER` no destino (se estiver divergente, abortar + alerta).
4. Executar rsync **somente** com allowlist.
5. Atualizar `upstream-docs/.sync-state.json` com:
   - `timestamp_utc`
   - `git_sha` (da raiz do workspace-main)
   - `manifest_sha`
   - `source=workspace-main`
   - `syncer=luan`
6. Executar checks de integridade (listar diffs inesperados).
7. Marcar destino em modo apenas leitura até próximo ciclo.

### 4.2 Gatilhos / frequência

- **Cron recomendado:** a cada 15 minutos (MVP), entre 08–23h UTC.
- **Sincronização sob demanda:** hook manual via comando:
  - `bash scripts/luan-upstream-docs-sync.sh --force`
- **Janela de segurança:** se `git status` de `workspace-main` está suja > 5 min, fazer `dry-run` apenas e registrar alerta (evita espelhar estado intermediário de trabalho).
- Alternativa futura: substituir por `git-sync` no trigger de mudança de branch/push (quando houver job dedicado). Mantido como opcional para estabilidade.

## 5) Read-only enforcement e anti-loop marker

### 5.1 Read-only

- Após o `rsync`, aplicar política de somente leitura:
  - `chmod -R a-w "$DST"`
  - manter flag de escrita apenas para o script de sync temporária (por exemplo ACL do próprio processo/usuário de automação)
- Luan não deve editar arquivos em `upstream-docs` manualmente.
- Luan pode ler, citar e propor mudanças, mas não versionar alterações locais nesse caminho.

### 5.2 Anti-loop marker

- Criar arquivo de controle em:
  - `upstream-docs/.luan-upstream-docs.marker`
- Conteúdo mínimo:
  - `source=workspace-main`
  - `source_git_sha=<sha>`
  - `syncer=luan`
  - `mode=read-only-mirror`
  - `last_sync_utc=<iso8601>`
- Regras:
  - Se `source` != `workspace-main`, **não executar sync** (protege contra push/pull invertido por engano).
  - Se `mode` != `read-only-mirror`, bloquear execução com falha explícita.
  - Em ambiente com múltiplas rotinas de sync, usar `flock` e check de timestamp para prevenir dupla execução.

## 6) Como Luan propõe mudanças (proposals / MC workflow)

Luan não altera `upstream-docs` diretamente. Mudanças reais seguem fluxo:

1. **Identificar necessidade** (docs/arquitetura/config que não está no mirror).
2. **Escrever proposta** curta em:
   - `docs/proposals/<slug>-<data>.md` (ex.: `docs/proposals/docs-upgrade-2026-03-04.md`) **na raiz de workspace-luan**, não no mirror.
3. **Encaminhar MC task** com:
   - path de origem exato no workspace-main
   - resumo objetivo
   - critério de aceite testável
4. **Aprovação/Merge no workspace principal** por processo normal.
5. **Mirror captura alteração no próximo ciclo** (incremental, sem editar diretamente).

Proposta deve incluir:
- impacto em arquitetura
- arquivos-alvo
- teste/validação esperada
- risco de rollback

## 7) Riscos e mitigação

| Risco | Impacto | Mitigação |
|---|---|---|
| Cópia de arquivos sensíveis | Exposição de segredos | allowlist rígido + exclusões explícitas de `*.env`, `credentials`, `openclaw.json`, tokens e DBs |
| Luan editar por engano o mirror | Conflito/overwrite local | Mirror em read-only + checks de tarefa (ciência de proposta obrigatória + revisão no `active-tasks`/MC) |
| Sincronização em estado não estável do main | Copiar estado quebrado/incompleto | Bloquear sync se branch sujo ou hash não converge; sincronizar só de fontes limpas |
| Loop entre syncs | Sobrecarga e conflitos de timestamp | Marker + lockfile (`flock`) + anti-loop validando `source` |
| Divergência entre mirror e origem | Luan referencia docs desatualizadas | Sync frequente + manifest hash + alerta em divergência |
| Overwrites em workspace-luan | Perda de mudanças locais fora do escopo | Mirror isolado em pasta separada e fora de paths editados normalmente por Luan |

## 8) Verificação (comandos)

### 8.1 Validação operacional

- Verificar se sync trouxe arquivos esperados:
  - `find upstream-docs/docs -maxdepth 2 -type f | head`
- Validar que `upstream-docs` está no conjunto correto:
  - `find upstream-docs -maxdepth 2 -type f \( -name '*.env*' -o -name '*.key' -o -name '*.pem' -o -path '*/openclaw.json' \)`
  - esperado: **sem saída**
- Validar controle de estado:
  - `cat upstream-docs/.sync-state.json`
  - `cat upstream-docs/.luan-upstream-docs.marker`
- Verificar modo somente leitura:
  - `stat -c '%A %a %n' upstream-docs` (esperado sem bit `w` para owner/grupo/others)
- Verificar drift: diff entre manifest e estado atual
  - `bash scripts/luan-upstream-docs-check.sh --verify`

### 8.2 Validação de anti-loop

- Executar sync duas vezes em sequência; a segunda deve ser incremental e não recriar todos os arquivos.
- Confirmar que arquivo marker impede sync reverso (modo de teste com `source` alterado deve falhar com log claro).

## 9) Rollback

- **Rollback imediato (2 minutos):** parar `cron`/trigger do sync + remover lock + restaurar estado anterior do ambiente de Luan (se necessário).
- **Rollback de estado:** manter último snapshot do mirror em `upstream-docs/.rollback/<timestamp>/` (hardlink ou tar).
- **Fallback:** remover pasta `upstream-docs` e desabilitar flag de sync no `.env`/script; continuar operação sem visibilidade do main até revisão.
- **Correção pós-falha:** validar MC task de revisão, ajustar allowlist/exclusões, reaplicar script de sync, revalidar com secções 8.1 e 8.2.

## 10) Entregáveis esperados (plano)

- Arquivo: `docs/luan-upstream-docs-mirror-plan.md`
- Scripts: apenas no namespace de planejamento (`scripts/luan-upstream-docs-sync.sh`, `scripts/luan-upstream-docs-check.sh`) **sem implementação nesta fase**
- Fluxo de operação: proposta → MC task → alteração no workspace-main → espelho incremental.

---

**Status:** `plan_submitted` (sem implementação)