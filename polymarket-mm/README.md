# Polymarket Market Maker

Maker-only bilateral quoting bot for Polymarket's CLOB.

## Quick Start

```bash
# Install dependencies
make install

# Run in paper mode (empty heartbeat loop)
make run-paper

# Start infrastructure (Postgres + Redis)
make up

# Run tests
make test

# Lint
make lint

# Stop infrastructure
make down
```

## Project Structure

```
polymarket-mm/
├── config/          # Pydantic settings, market configs
├── core/            # Entrypoint, logger, event bus, heartbeat
├── models/          # Domain models (MarketState, QuotePlan, Order, Position)
├── data/            # Market data: WS client, REST client, oracle collectors
├── strategy/        # Quote engine, inventory skew, spread model, toxic flow
├── execution/       # Order management, quantizer, queue tracker
├── web3_infra/      # CTF adapter, EIP-712 signer, RPC manager
├── storage/         # Memory store, cold writer, migrations
├── monitoring/      # Prometheus metrics, health endpoint, alerts
├── ai_copilot/      # Post-mortem, param tuner, anomaly detector
├── paper/           # Paper venue, chaos injector, replay engine
└── tests/           # Unit and property-based tests
```

## Configuration

All settings are managed via environment variables (see `.env.example`).
Monetary values use `Decimal` — **never** `float`.

## Phases

See `docs/polymarket-mm-plan.md` for the full 11-phase roadmap.
Currently: **Fase 0 — Bootstrap Infra** (empty heartbeat loop).
