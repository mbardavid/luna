# ClawSuite — Relatório de Revisão do Repositório

**Repositório:** https://github.com/outsourc-e/clawsuite  
**Data da análise:** 2026-03-01  
**Versão analisada:** 3.2.0 (package.json) / 3.0.0 (último release documentado no CHANGELOG)  
**Licença:** MIT  
**Autor:** Eric (@outsourc-e)

---

## 1. O que é / Objetivo

ClawSuite é uma **plataforma full-stack de "mission control"** para agentes AI do ecossistema OpenClaw. Posiciona-se como "o VSCode para agentes AI" — um centro de comando completo que permite:

- Orquestrar múltiplos agentes AI (spawn, pause, resume, abort)
- Conversar com agentes via chat em tempo real com streaming de tokens
- Monitorar custos por agente, por modelo e por período
- Gerenciar arquivos do workspace, memória dos agentes, skills e cron jobs
- Aprovar/negar execuções sensíveis (exec approval) via interface visual
- Acessar terminal integrado e browser embutido

**Não é um wrapper de chat.** É uma interface completa de desenvolvimento e operação de agentes AI, com dashboard customizável, analytics de custos, e ferramentas de desenvolvedor integradas.

**Tagline oficial:** *"Not a chat wrapper. A complete command center."*

---

## 2. Arquitetura

### Stack Tecnológico

| Camada | Tecnologia |
|--------|-----------|
| **Frontend** | React 19 + TypeScript |
| **Roteamento** | TanStack Router (file-based routing) |
| **Estado (server)** | TanStack Query (React Query v5) |
| **Estado (client)** | Zustand |
| **Full-stack Framework** | TanStack Start (SSR) |
| **Styling** | Tailwind CSS v4 + class-variance-authority |
| **Editor de código** | Monaco Editor (@monaco-editor/react) |
| **Terminal** | xterm.js (com addons fit, search, web-links) |
| **Gráficos** | Recharts |
| **Animações** | Motion (Framer Motion) |
| **Markdown** | react-markdown + remark-gfm + shiki (syntax highlight) |
| **Validação** | Zod |
| **Build** | Vite 7 |
| **Testes** | Vitest + Testing Library |
| **Lint/Format** | ESLint + Prettier |
| **Browser automação** | Playwright + playwright-extra + puppeteer-stealth |
| **WebSocket** | ws (comunicação com gateway) |
| **Containerização** | Docker (multi-stage build, Node 22 Alpine) |
| **CI/CD** | GitHub Actions (ci.yml, release.yml, security.yml) |
| **Runtime** | Node.js 22+ |

### Estrutura de Diretórios

```
clawsuite/
├── .github/workflows/       # CI, release, security workflows
├── docs/                    # Documentação de arquitetura e roadmap
│   ├── ARCHITECTURE.md
│   ├── CLAWSUITE-ARCHITECTURE.md  # Arquitetura detalhada (17k chars)
│   ├── CLOUD-VISION.md
│   ├── PRODUCT-ROADMAP.md
│   ├── gateway-setup-wizard.md
│   └── mobile-setup.md
├── public/                  # Assets estáticos (logos, ícones PWA, screenshots)
├── src/
│   ├── components/          # Componentes React reutilizáveis
│   ├── hooks/               # Custom hooks React
│   ├── lib/                 # Utilitários e bibliotecas internas
│   ├── routes/              # Rotas TanStack Router (file-based)
│   │   ├── api/             # Rotas de API server-side (SSR)
│   │   ├── __root.tsx       # Layout raiz
│   │   ├── activity.tsx
│   │   ├── agent-swarm.tsx
│   │   ├── agents.tsx
│   │   ├── browser.tsx
│   │   ├── channels.tsx
│   │   ├── chat/            # Rotas de chat
│   │   ├── connect.tsx
│   │   ├── costs.tsx
│   │   └── ...
│   ├── screens/             # Telas completas da aplicação
│   │   ├── activity/        # Tela de atividade/logs
│   │   ├── chat/            # Interface de chat
│   │   ├── costs/           # Analytics de custos
│   │   ├── cron/            # Gerenciador de cron jobs
│   │   ├── dashboard/       # Dashboard principal com widgets
│   │   ├── debug/           # Console de debug/diagnóstico
│   │   ├── files/           # File browser do workspace
│   │   ├── gateway/         # Configuração do gateway
│   │   ├── memory/          # Browser de memória dos agentes
│   │   ├── settings/        # Configurações
│   │   ├── skills/          # Marketplace de skills
│   │   └── tasks/           # Board de tarefas/missões
│   ├── server/              # Lógica server-side
│   │   ├── auth-middleware.ts
│   │   ├── activity-stream.ts     # SSE streaming
│   │   ├── browser-monitor.ts     # Monitor do browser
│   │   ├── browser-proxy.ts       # Proxy do browser
│   │   ├── browser-session.ts     # Sessões do browser
│   │   ├── browser-stream.ts      # Stream do browser
│   │   ├── cron.ts                # CRUD de cron jobs
│   │   ├── debug-analyzer.ts      # Diagnóstico do gateway
│   │   ├── exec-approval-store.ts # Store de aprovações
│   │   └── ...
│   ├── stores/              # Zustand stores (estado global client)
│   ├── types/               # Definições TypeScript
│   ├── utils/               # Funções utilitárias
│   ├── styles.css           # Estilos globais (26k chars)
│   ├── router.tsx           # Configuração do router
│   └── routeTree.gen.ts     # Árvore de rotas gerada automaticamente
├── Dockerfile               # Build multi-stage para produção
├── docker-compose.yml       # Compose para deploy
├── server-entry.js          # Entry point do servidor Node
├── package.json
├── AGENTS.md                # Instruções para agentes AI que trabalham no repo
├── CHANGELOG.md
├── CONTRIBUTING.md
├── SECURITY.md
├── ROADMAP.md
└── LICENSE (MIT)
```

### Padrão Arquitetural

A aplicação usa **TanStack Start** como framework full-stack, combinando:

1. **Server-side rendering (SSR)** — rotas de API em `src/routes/api/` processadas no servidor
2. **Client-side routing** — navegação SPA com TanStack Router
3. **Server Functions** — comunicação server↔client via TanStack Start
4. **SSE (Server-Sent Events)** — streaming em tempo real do output dos agentes
5. **WebSocket** — comunicação bidirecional com o OpenClaw Gateway

O server atua como **proxy seguro** entre o browser do usuário e o OpenClaw Gateway, mantendo tokens e credenciais exclusivamente no servidor.

---

## 3. Módulos Principais

### 3.1 Dashboard (`src/screens/dashboard/`)
Dashboard customizável com widgets arrastáveis (react-grid-layout). Inclui:
- KPIs de custo (MTD, projeção EOM, budget %)
- Agentes ativos e status
- Quick actions para operações comuns
- Métricas do sistema (CPU, RAM, disco, uptime)

### 3.2 Chat (`src/screens/chat/`)
Interface de chat em tempo real com agentes AI:
- Streaming de tokens via SSE (sem polling)
- Gerenciamento multi-sessão com histórico completo
- Upload de arquivos e imagens
- Markdown + syntax highlighting (shiki)
- Busca de mensagens (Cmd+F)

### 3.3 Mission Control / Agent Hub (`src/screens/tasks/`, `src/routes/agents.tsx`, `src/routes/agent-swarm.tsx`)
Orquestração completa de agentes:
- Spawn, pause, resume, abort de agentes
- Visualização isométrica "office view" com agentes trabalhando
- Streaming de output por agente via SSE
- Exec approval — aprovar/negar comandos sensíveis com modal UI, countdown de 30s, risk badges
- Mission reports com taxa de sucesso, contagem de tokens e artefatos

### 3.4 Cost Analytics (`src/screens/costs/`)
Rastreamento de custos com dados reais do SQLite do gateway:
- Breakdown por agente e por modelo
- Tendências diárias (30 dias)
- Projeções de fim de mês
- Suporte a múltiplos providers (OpenAI, Anthropic, Google, etc.)

### 3.5 Memory Browser (`src/screens/memory/`)
Navegação e edição dos arquivos de memória dos agentes:
- Lista agrupada de arquivos
- Busca full-text com salto para linha
- Modo de edição com indicador de alterações não salvas
- Toggle de preview markdown

### 3.6 File Browser (`src/screens/files/`)
Explorador de arquivos do workspace:
- Árvore de arquivos expansível
- Editor Monaco (VSCode core) para edição inline
- Preview de imagens e markdown
- Syntax highlighting para TS/JS/JSON
- Upload/download

### 3.7 Skills Marketplace (`src/screens/skills/`)
Navegação e instalação de skills do ecossistema OpenClaw:
- 2.000+ skills do registry ClawdHub
- Escaneamento de segurança antes da instalação
- Instalação one-click com resolução de dependências

### 3.8 Cron Manager (`src/screens/cron/`)
Gerenciamento de tarefas agendadas:
- CRUD completo de cron jobs
- Campo `nextRunAt` para visualização de próxima execução

### 3.9 Browser Integrado (`src/routes/browser.tsx`, `src/server/browser-*.ts`)
Browser Chromium embutido:
- Stealth anti-detection (via puppeteer-extra-plugin-stealth)
- Sessões persistentes (cookies sobrevivem restarts)
- Handoff de páginas para agentes AI
- Proxy e streaming via módulos server-side dedicados

### 3.10 Debug Console (`src/screens/debug/`)
Ferramentas de diagnóstico:
- Debug analyzer (análise de padrões de erro)
- Diagnóstico do gateway
- Troubleshooting guiado

### 3.11 Server/Backend (`src/server/`)
Camada server-side com módulos dedicados:
- **auth-middleware.ts** — autenticação em todas as rotas API
- **activity-stream.ts** — SSE streaming de eventos dos agentes
- **exec-approval-store.ts** — store de aprovações de execução
- **browser-proxy.ts** / **browser-session.ts** / **browser-stream.ts** — gerenciamento completo do browser
- **cron.ts** — lógica de CRUD de cron jobs
- **debug-analyzer.ts** — análise e diagnóstico

### 3.12 PWA (Progressive Web App)
- Instalável como app nativo em iOS, Android, macOS, Windows, Linux
- Ícones PWA (192px, 512px)
- Suporte offline básico
- Service worker

---

## 4. Comandos / CLI

### Scripts npm (package.json)

| Comando | Descrição |
|---------|-----------|
| `npm run dev` | Inicia em modo desenvolvimento na porta 3000 (`vite dev --port 3000`) |
| `npm run build` | Build de produção (`vite build`) |
| `npm run preview` | Preview do build de produção |
| `npm run test` | Executa testes (`vitest run`) |
| `npm run lint` | Lint com ESLint |
| `npm run format` | Formata com Prettier |
| `npm run check` | Prettier + ESLint com auto-fix |
| `npm run beta:reset-state` | Reset do estado local (script bash para beta testers) |
| `npm run beta:export-diagnostics` | Exporta diagnósticos (script bash para beta testers) |

### Docker

```bash
# Build e run via Docker Compose
docker compose up -d

# Build manual
docker build -t clawsuite .
docker run -p 3000:3000 -e CLAWDBOT_GATEWAY_URL=ws://host.docker.internal:18789 clawsuite
```

---

## 5. Setup / Instalação

### Pré-requisitos
- **Node.js 22+**
- **OpenClaw Gateway** rodando localmente (porta padrão 18789)

### Instalação

```bash
git clone https://github.com/outsourc-e/clawsuite.git
cd clawsuite
npm install
cp .env.example .env    # Configurar gateway URL + tokens
npm run dev             # http://localhost:3000
```

### Variáveis de Ambiente (.env)

| Variável | Descrição | Obrigatório |
|----------|-----------|:-----------:|
| `CLAWDBOT_GATEWAY_URL` | URL WebSocket do gateway (default: `ws://127.0.0.1:18789`) | ✅ |
| `CLAWDBOT_GATEWAY_TOKEN` | Token de autenticação do gateway (formato `clw_...`) | ✅* |
| `CLAWDBOT_GATEWAY_PASSWORD` | Senha alternativa ao token | ✅* |
| `CLAWSUITE_PASSWORD` | Senha para proteger a interface web | ❌ |
| `CLAWSUITE_ALLOWED_HOSTS` | Hosts permitidos (Tailscale, LAN, etc.) | ❌ |

\* Um dos dois (token ou password) é necessário.

### Deploy Docker

O Dockerfile usa multi-stage build:
1. **builder** — Instala dependências e faz build com Vite
2. **skills** — Baixa skills built-in do OpenClaw via npm
3. **runner** — Imagem final com Node 22 Alpine, usuário não-root, porta 3000

### Acesso Mobile (via Tailscale)
Suporte documentado para acesso remoto via Tailscale, permitindo usar o ClawSuite de qualquer dispositivo sem port forwarding.

---

## 6. Casos de Uso

### Quem usaria

1. **Desenvolvedores que usam OpenClaw** — principal público-alvo. Qualquer pessoa que opera agentes AI via OpenClaw e quer uma interface visual
2. **Equipes de AI Engineering** — para monitorar custos, aprovar execuções, e coordenar múltiplos agentes
3. **Power users de AI** — que querem transparência total sobre o que seus agentes fazem (vs. "black box" do ChatGPT)

### Cenários práticos

- **Orquestração de agentes:** Lançar missões com múltiplos agentes, monitorar progresso em tempo real, aprovar/negar comandos sensíveis
- **Controle de custos:** Visualizar quanto cada agente gasta por dia, projetar custos mensais, identificar modelos mais caros
- **Desenvolvimento:** Editar arquivos do workspace, usar terminal integrado, debugar problemas do gateway
- **Gerenciamento de memória:** Visualizar e editar os arquivos de memória dos agentes diretamente na interface
- **Automação:** Configurar cron jobs para tarefas recorrentes dos agentes
- **Navegação assistida:** Usar browser embutido com anti-detection para tarefas que exigem navegação web

---

## 7. Riscos e Preocupações

### 🟢 Pontos Positivos

- **Licença MIT** — uso livre, sem restrições
- **Segurança razoável** — auth em todas as rotas, CSRF guards, rate limiting, path traversal prevention, exec approval workflow
- **Bem documentado** — README completo, ARCHITECTURE.md detalhado, SECURITY.md com audit trail, CONTRIBUTING.md
- **CI/CD configurado** — workflows de CI, release e security scanning
- **Docker pronto** — multi-stage build com usuário não-root
- **Testes configurados** — Vitest + Testing Library (embora cobertura não esteja documentada)

### 🟡 Pontos de Atenção

1. **Dependência forte do OpenClaw Gateway** — ClawSuite é inútil sem o gateway rodando. Fortemente acoplado ao ecossistema OpenClaw
2. **Playwright em produção** — `playwright` e `playwright-extra` estão em `dependencies` (não `devDependencies`), adicionando ~300MB+ ao node_modules. O Dockerfile pula o download de browsers, mas a dependência persiste
3. **puppeteer-extra-plugin-stealth** — Plugin de anti-detection em produção. Pode levantar questões éticas/legais dependendo do uso (bypass de bot detection de sites terceiros)
4. **Repo relativamente novo** — v1.0 lançada em 2026-02-17 (2 semanas antes desta análise). Ainda em maturação rápida
5. **`private: true` no package.json** — Marcado como privado, não publicável no npm
6. **routeTree.gen.ts com 81KB** — Arquivo gerado automaticamente bastante grande, commitado no repo

### 🔴 Riscos

1. **Exec approval com auto-deny em 30s** — Se o usuário não estiver olhando, comandos sensíveis são negados automaticamente. Poderia haver um mecanismo mais robusto (ex: queue sem timeout)
2. **Browser proxy como attack surface** — O browser embutido com Playwright + stealth rodando no server é uma superfície de ataque significativa. CORS está restrito a localhost, mas qualquer vulnerabilidade de SSRF poderia ser explorada
3. **Rate limiting baseado em IP** — 10 req/min em endpoints de alto risco. Para uso local (localhost), todos os requests vêm do mesmo IP — o rate limiter pode não ser efetivo
4. **Sem autenticação multi-fator** — Apenas password simples. Para deployments remotos (Tailscale, LAN), isso pode ser insuficiente
5. **Gateway token no .env** — Embora server-side only, o token do gateway fica em plaintext no arquivo .env. Não há integração com secret managers
6. **Versão do documento de arquitetura desatualizada** — `CLAWSUITE-ARCHITECTURE.md` marca "Version 2.0.0" mas o app está na 3.x. Pode gerar confusão

### 📦 Dependências Notáveis

- **react@19.2.0** — Versão muito recente do React 19
- **vite@7.1.7** — Vite 7 (cutting edge)
- **tailwindcss@4.1.18** — Tailwind v4 (nova arquitetura)
- **@tanstack/react-start** — Framework full-stack relativamente novo
- Todas as dependências estão em versões muito recentes, o que pode significar APIs instáveis ou breaking changes frequentes

### 🔐 Segurança — Resumo

O projeto passou por pelo menos uma auditoria de segurança documentada (SEC-3, 2026-02-25) que cobriu:
- Auth guards em todas as rotas API
- CSRF protection via content-type enforcement
- Rate limiting em endpoints de alto risco
- Path traversal prevention
- CORS restrito a localhost
- Política de responsible disclosure documentada em SECURITY.md
- Email de segurança: security@clawsuite.io

---

## Resumo Executivo

ClawSuite é uma plataforma web completa e bem construída para gerenciar agentes AI do OpenClaw, com dashboard, chat, mission control, cost analytics, e ferramentas de dev integradas. Usa stack moderna (React 19, TanStack Start, Tailwind v4, Vite 7) com boas práticas de segurança. Principal risco é o acoplamento ao ecossistema OpenClaw e o uso de dependências cutting-edge que podem ser instáveis. Licença MIT, código aberto, e em desenvolvimento ativo com releases frequentes.
