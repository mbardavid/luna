# Plan: Era 3 Agent Architecture — From Spawned Sessions to Persistent Agent Fleet

**Status:** DRAFT — awaiting Matheus review
**Created:** 2026-03-01
**Context:** [Michael Truell (Cursor CEO) — "The Third Era of AI Software Development"](https://x.com/mntruell/status/2026736314272591924)

---

## 1. The Article (Key Takeaways)

Michael Truell descreve 3 eras de desenvolvimento com IA:

| Era | Modo | Papel do Dev |
|-----|------|-------------|
| **1. Tab** | Autocomplete | Escreve código, IA completa |
| **2. Agents síncronos** | Prompt → resposta | Dirige agente passo a passo |
| **3. Fleet de agents** | Agents autônomos em cloud | Define problema + review criteria, spawna múltiplos agents, revisa artifacts |

**Dado crítico:** 35% dos PRs merged no Cursor já são de agents autônomos em VMs cloud.

**Implicação:** O dev para de escrever código e passa a gerenciar uma **fábrica de software** — fleet de agents com direção inicial, ferramentas, e review.

---

## 2. Onde Estamos (Gap Analysis)

### O que temos:
- ✅ Luna como orquestrador central
- ✅ Mission Control para tracking de tasks
- ✅ `sessions_spawn` para delegar trabalho
- ✅ Luan como "agente de código"
- ✅ Auto-announce de completions

### O gap (que o Matheus identificou):
- ❌ **Luan não é um agente real** — é apenas uma sessão spawn com as mesmas instruções da Luna. Sem memória própria, sem lessons learned, sem SOUL.md customizado para código
- ❌ **Sem agent loops** — cada spawn é one-shot. Luan não itera, não roda testes repetidamente, não faz TDD cycles
- ❌ **Sem persistent workspace** — Luan não tem um workspace-luan com estado persistente entre tasks
- ❌ **Sem review automatizado** — Luna confia cegamente no output do Luan. Não roda testes independentemente, não faz code review
- ❌ **Sem paralelismo real** — spawamos Luans sequencialmente, não 5 em paralelo
- ❌ **Sem artifacts ricos** — Luan reporta texto. Deveria produzir: test results, diffs, screenshots, benchmarks
- ❌ **Sem self-healing** — se Luan falha, Luna notifica mas não re-spawna com ajustes automaticamente
- ❌ **Sem specialization** — Luan faz tudo (Python, JS, infra, tests). Agentes especializados seriam mais eficazes

---

## 3. Arquitetura Proposta: Era 3

### 3.1 Agentes Persistentes com Identidade

Cada agente deve ter:

```
workspace-<agent>/
├── SOUL.md          # Personalidade, estilo, especializações
├── AGENTS.md        # Regras operacionais
├── MEMORY.md        # Memória de longo prazo
├── TOOLS.md         # Ferramentas e configs locais
├── memory/
│   ├── lessons.md   # Erros que não deve repetir
│   └── YYYY-MM-DD.md
└── .agent-config.json  # Model, capabilities, limits
```

**Agents propostos:**

| Agent | Especialização | Model |
|-------|---------------|-------|
| **Luan** | Python/backend, PMM, testing | Claude Opus |
| **Frontend** (novo) | Dashboard, UI, HTML/CSS/JS | Claude Opus ou Sonnet |
| **Infra** (novo) | DevOps, Docker, systemd, monitoring | Claude Opus |
| **Reviewer** (novo) | Code review, test validation, security audit | Claude Opus (read-only) |

### 3.2 Agent Loop (Inner Loop)

Cada agent deve ter um **inner loop** antes de reportar conclusão:

```
1. Receive task spec
2. Read lessons.md (avoid past mistakes)
3. Plan approach
4. Implement
5. Run tests
6. If tests fail → iterate (max 3 cycles)
7. Run linter/type check
8. Generate artifacts (test report, diff summary, coverage delta)
9. Update lessons.md if learned something
10. Report with structured output
```

**Atualmente:** Steps 1, 4, 5, 10 (parcial)
**Meta:** Todos os 10 steps

### 3.3 Task Spec Structure (Padronizada)

Toda task spawned deve seguir este formato:

```yaml
task:
  title: "Fix production runner: trade dedup"
  type: bugfix|feature|refactor|research|review
  workspace: /path/to/workspace
  files:
    - paper/production_runner.py
    - tests/test_production_runner.py
  acceptance_criteria:
    - All existing tests pass
    - New tests cover the fix
    - No regressions in test count
  constraints:
    - Don't modify kill switch logic
    - Keep backward compatibility
  artifacts_required:
    - test_report: pytest output
    - diff_summary: files changed + lines
    - coverage_delta: if applicable
```

### 3.4 Review Pipeline (Automated)

Ao invés de confiar cegamente no Luan:

```
Luan completes → Luna verifies:
  1. Run pytest independently
  2. Check test count (should increase or stay same)
  3. Check for common anti-patterns (e.g. hardcoded values, missing error handling)
  4. Compare diff size vs task complexity (flag suspiciously small or large diffs)
  5. If all pass → merge to MC as done
  6. If fail → re-spawn with specific feedback
```

### 3.5 Parallel Execution

Atualmente: 1 Luan por vez (gateway limita por agentId)
Meta: múltiplos agents simultâneos

**Approach:**
- Cada agent type tem seu próprio agentId no openclaw.json
- Luna pode spawnar frontend + backend + reviewer em paralelo
- MC tracks all in parallel
- Luna reviews artifacts as they complete

### 3.6 Self-Healing / Auto-Retry

```python
# Pseudo-code for Luna's spawn-and-verify loop
result = spawn(luan, task)
if result.tests_failed:
    # Re-spawn with failure context
    result = spawn(luan, task + f"\nPrevious attempt failed:\n{result.error}\nFix this specific issue.")
if result.tests_failed_again:
    # Escalate to human
    notify(matheus, "Task failed 2x, needs human review")
```

---

## 4. Implementation Phases

### Phase 1: Luan Identity (1-2 days)
- [ ] Create proper `workspace-luan/SOUL.md` — personality focused on code quality, TDD, systematic debugging
- [ ] Create `workspace-luan/AGENTS.md` — inner loop protocol, test requirements
- [ ] Create `workspace-luan/memory/lessons.md` — seed with existing lessons (complement routing, sync fills, etc.)
- [ ] Update openclaw.json: Luan agent with `workspace: workspace-luan`
- [ ] Luan reads his own SOUL.md/lessons.md at start of each session

### Phase 2: Structured Task Specs (1 day)
- [ ] Create task spec template at `docs/task-spec-template.yaml`
- [ ] Update `mc-spawn.sh` to accept structured spec
- [ ] Luna generates structured specs instead of free-text descriptions
- [ ] Include acceptance criteria and required artifacts

### Phase 3: Inner Loop (2-3 days)
- [ ] Luan's AGENTS.md instructs: "run tests before reporting"
- [ ] Luan iterates on test failures (max 3 cycles)
- [ ] Luan outputs structured completion report (JSON):
  ```json
  {
    "status": "complete",
    "tests": {"passed": 604, "failed": 0, "new": 12},
    "files_changed": ["file1.py", "file2.py"],
    "lines_added": 150,
    "lines_removed": 30,
    "lessons_learned": ["USDC balance from API is in micro-units, always divide by 1e6"]
  }
  ```
- [ ] Luna parses structured output for MC updates

### Phase 4: Automated Review (2 days)
- [ ] Luna runs `pytest` independently after Luan completes
- [ ] Luna diffs test count (before vs after)
- [ ] Luna does quick code review (scan for anti-patterns)
- [ ] Auto-approve if all checks pass
- [ ] Re-spawn with feedback if checks fail

### Phase 5: Multi-Agent Fleet (3-5 days)
- [ ] Register additional agents in openclaw.json (reviewer, frontend)
- [ ] Create workspaces with specialized SOUL.md for each
- [ ] Luna orchestrates parallel spawns (e.g. "Luan fix backend, Frontend fix dashboard")
- [ ] MC tracks all simultaneously
- [ ] Luna reviews and merges outputs

### Phase 6: Continuous Improvement (ongoing)
- [ ] Agents accumulate lessons.md over time
- [ ] Daily memory consolidation per agent
- [ ] Performance metrics: task completion rate, retry rate, average time
- [ ] Luna learns which agent types work best for which tasks
- [ ] Matheus reviews agent fleet performance weekly

---

## 5. Success Metrics

| Metric | Current | Target (30 days) |
|--------|---------|-------------------|
| Tasks completed/day | 2-3 | 8-10 |
| First-attempt success rate | ~60% | >85% |
| Test regressions per task | ~20% chance | <5% |
| Average task completion time | 15min | 8min |
| Parallel agents | 1 | 3-4 |
| Human review time per task | 5min reading text | 30s reviewing artifacts |
| Agent lesson retention | 0 (lost each session) | Persistent across sessions |

---

## 6. Key Insight from the Article

> "The human role shifts from guiding each line of code to defining the problem and setting review criteria."

Isso é exatamente o que o Matheus quer. Hoje ele ainda precisa:
- Diagnosticar bugs no dashboard manualmente
- Ler outputs longos de texto
- Religar processos quando crasham

Na Era 3, ele deveria:
- Definir "dashboard deve mostrar PnL real" como acceptance criteria
- Spawnar agent fleet
- Receber artifacts (screenshot do dashboard, test report, diff)
- Aprovar ou rejeitar em 30 segundos

---

## 7. Riscos e Mitigações

| Risco | Mitigação |
|-------|-----------|
| Agents divergem sem supervisão | Review pipeline obrigatório |
| Lessons.md fica stale | Consolidação semanal automática |
| Custos de tokens explodem com parallelismo | Budget caps por agent por dia |
| Conflitos de merge entre agents paralelos | File-level locking ou task isolation |
| Agent loop infinito | Max 3 iterations, timeout 15min |
| Qualidade degrada com velocidade | Test count must increase, never decrease |

---

## Next Action

Matheus revisa este plano e decide:
1. Começar por qual fase?
2. Concordo com os agents propostos?
3. Budget de tokens por dia?
4. Quer que eu comece Phase 1 (Luan Identity) imediatamente?
