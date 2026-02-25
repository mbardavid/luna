"""ParamTuner — Bayesian optimization of strategy parameters.

Uses Optuna for hyperparameter tuning of key strategy parameters:
γ (risk aversion), spread_min, volatility multiplier, etc.

**Important:** This module only **proposes** parameter adjustments.
A human must approve before any changes are applied.  The tuner
never modifies live settings.

Usage::

    tuner = ParamTuner()
    suggestions = tuner.optimise(
        historical_fills=fills,
        historical_positions=positions,
        n_trials=50,
    )
    for s in suggestions:
        print(f"{s.param_name}: {s.current_value} → {s.suggested_value}")
        print(f"  Reason: {s.reason}")
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Sequence

import optuna
import structlog

from ai_copilot.post_mortem import FillRecord, PositionSnapshot

logger = structlog.get_logger("ai_copilot.param_tuner")

_ZERO = Decimal("0")


# ── Configuration ────────────────────────────────────────────────────


@dataclass
class ParamRange:
    """Defines the search space for a single parameter."""

    name: str
    low: float
    high: float
    current: float
    log_scale: bool = False
    description: str = ""


@dataclass
class ParamTunerConfig:
    """Configuration for the parameter tuner.

    Attributes
    ----------
    param_ranges:
        Search space definitions.  Defaults cover the core A-S parameters.
    n_trials:
        Number of Optuna trials per optimisation run.
    sampler_seed:
        Random seed for reproducibility.
    direction:
        Optuna optimisation direction (``"maximize"`` for PnL-based objectives).
    """

    param_ranges: list[ParamRange] = field(default_factory=lambda: [
        ParamRange(
            name="gamma_risk_aversion",
            low=0.05, high=1.0, current=0.3,
            description="Avellaneda-Stoikov risk aversion (γ)",
        ),
        ParamRange(
            name="min_half_spread_bps",
            low=5.0, high=100.0, current=15.0,
            description="Minimum half-spread in basis points",
        ),
        ParamRange(
            name="vol_multiplier",
            low=0.5, high=5.0, current=1.5,
            description="Volatility-to-spread multiplier",
        ),
        ParamRange(
            name="time_horizon_hours",
            low=1.0, high=72.0, current=24.0,
            description="Time horizon for inventory skew decay",
        ),
        ParamRange(
            name="max_skew",
            low=0.01, high=0.25, current=0.10,
            description="Maximum absolute skew in price units",
        ),
    ])
    n_trials: int = 50
    sampler_seed: int = 42
    direction: str = "maximize"


# ── Suggestion output ────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ParamSuggestion:
    """A single parameter adjustment suggestion.

    The tuner produces a list of these; a human reviews and decides
    whether to apply each one.
    """

    param_name: str
    current_value: float
    suggested_value: float
    expected_improvement_pct: float
    reason: str
    confidence: str  # "low", "medium", "high"

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict."""
        return asdict(self)


@dataclass
class TunerResult:
    """Complete tuning result with all suggestions and metadata."""

    suggestions: list[ParamSuggestion] = field(default_factory=list)
    best_objective_value: float = 0.0
    baseline_objective_value: float = 0.0
    n_trials_completed: int = 0
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_json(self, indent: int = 2) -> str:
        """Serialise to JSON string."""

        def _convert(obj: Any) -> Any:
            if isinstance(obj, Decimal):
                return str(obj)
            if isinstance(obj, datetime):
                return obj.isoformat()
            if isinstance(obj, dict):
                return {k: _convert(v) for k, v in obj.items()}
            if isinstance(obj, (list, tuple)):
                return [_convert(i) for i in obj]
            return obj

        return json.dumps(_convert(asdict(self)), indent=indent, ensure_ascii=False)

    def to_markdown(self) -> str:
        """Generate human-readable Markdown summary."""
        lines = [
            "# Parameter Tuning Results",
            "",
            f"**Generated:** {self.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"**Trials:** {self.n_trials_completed}",
            f"**Baseline Score:** {self.baseline_objective_value:.4f}",
            f"**Best Score:** {self.best_objective_value:.4f}",
            "",
            "## Suggestions",
            "",
            "| Parameter | Current | Suggested | Expected Improvement | Confidence |",
            "|-----------|---------|-----------|---------------------|------------|",
        ]
        for s in self.suggestions:
            lines.append(
                f"| {s.param_name} | {s.current_value:.4f} | "
                f"{s.suggested_value:.4f} | {s.expected_improvement_pct:+.1f}% | "
                f"{s.confidence} |"
            )
        lines.append("")
        for s in self.suggestions:
            lines.append(f"**{s.param_name}:** {s.reason}")
            lines.append("")
        return "\n".join(lines)


# ── Objective function ───────────────────────────────────────────────


def _default_objective(
    fills: Sequence[FillRecord],
    positions: Sequence[PositionSnapshot],
    params: dict[str, float],
) -> float:
    """Default objective function: risk-adjusted PnL proxy.

    Computes a simplified Sharpe-like metric from fills data and
    the candidate parameter set.  In production, this should be
    replaced with a more sophisticated backtesting function.

    Parameters
    ----------
    fills:
        Historical fill records.
    positions:
        End-of-period position snapshots.
    params:
        Candidate parameter values from the optimiser.

    Returns
    -------
    float
        Objective value to maximise (higher is better).
    """
    if not fills:
        return 0.0

    gamma = params.get("gamma_risk_aversion", 0.3)
    min_hs = params.get("min_half_spread_bps", 15.0)

    # Simulate PnL with parameter-dependent adjustments
    pnl_values: list[float] = []
    cumulative = 0.0

    for f in fills:
        notional = float(f.price * f.size)
        fee = float(f.fee)

        # Spread capture component
        spread_edge = min_hs / 10000.0 * notional

        if f.side == "SELL":
            cumulative += notional - fee + spread_edge
        else:
            cumulative -= notional + fee - spread_edge * 0.5

        pnl_values.append(cumulative)

    if not pnl_values:
        return 0.0

    # Risk adjustment: penalise high inventory (using gamma)
    total_pnl = pnl_values[-1]
    inventory_penalty = 0.0
    for p in positions:
        net_inv = abs(float(p.qty_yes - p.qty_no))
        inventory_penalty += gamma * net_inv * 0.001

    # Volatility of PnL (for Sharpe-like metric)
    if len(pnl_values) > 1:
        diffs = [pnl_values[i] - pnl_values[i - 1] for i in range(1, len(pnl_values))]
        avg_ret = sum(diffs) / len(diffs)
        var = sum((d - avg_ret) ** 2 for d in diffs) / len(diffs)
        vol = var ** 0.5
        sharpe = total_pnl / vol if vol > 0 else total_pnl
    else:
        sharpe = total_pnl

    return sharpe - inventory_penalty


# ── ParamTuner ───────────────────────────────────────────────────────


class ParamTuner:
    """Bayesian optimizer for strategy parameters using Optuna.

    The tuner runs a configurable number of trials, evaluating candidate
    parameter sets against a supplied objective function (or a sensible
    default).  It produces ``ParamSuggestion`` objects that a human
    reviews before applying.

    Parameters
    ----------
    config:
        Tuner configuration (search space, trial count, etc.).
    objective_fn:
        Custom objective function.  Signature:
        ``(fills, positions, params) -> float``.
        If ``None``, uses the built-in risk-adjusted PnL proxy.
    """

    def __init__(
        self,
        config: ParamTunerConfig | None = None,
        objective_fn: Callable[
            [Sequence[FillRecord], Sequence[PositionSnapshot], dict[str, float]],
            float,
        ]
        | None = None,
    ) -> None:
        self._config = config or ParamTunerConfig()
        self._objective_fn = objective_fn or _default_objective

    @property
    def config(self) -> ParamTunerConfig:
        """Return current configuration."""
        return self._config

    def optimise(
        self,
        historical_fills: Sequence[FillRecord],
        historical_positions: Sequence[PositionSnapshot],
        n_trials: int | None = None,
    ) -> TunerResult:
        """Run Bayesian optimisation and return suggested parameter changes.

        Parameters
        ----------
        historical_fills:
            Fill records from the evaluation period.
        historical_positions:
            Position snapshots from the evaluation period.
        n_trials:
            Override for number of trials (defaults to config value).

        Returns
        -------
        TunerResult
            Optimisation results with ``ParamSuggestion`` entries for
            parameters where the optimiser found meaningful improvements.
        """
        cfg = self._config
        trials = n_trials or cfg.n_trials

        # Compute baseline with current params
        current_params = {pr.name: pr.current for pr in cfg.param_ranges}
        baseline = self._objective_fn(
            historical_fills, historical_positions, current_params
        )

        # Silence Optuna logs during optimisation
        optuna.logging.set_verbosity(optuna.logging.WARNING)

        sampler = optuna.samplers.TPESampler(seed=cfg.sampler_seed)
        study = optuna.create_study(
            direction=cfg.direction,
            sampler=sampler,
        )

        def objective(trial: optuna.Trial) -> float:
            params: dict[str, float] = {}
            for pr in cfg.param_ranges:
                if pr.log_scale:
                    params[pr.name] = trial.suggest_float(
                        pr.name, pr.low, pr.high, log=True
                    )
                else:
                    params[pr.name] = trial.suggest_float(
                        pr.name, pr.low, pr.high
                    )
            return self._objective_fn(
                historical_fills, historical_positions, params
            )

        study.optimize(objective, n_trials=trials, show_progress_bar=False)

        # Build suggestions
        best_params = study.best_params
        best_value = study.best_value

        suggestions: list[ParamSuggestion] = []
        for pr in cfg.param_ranges:
            suggested = best_params.get(pr.name, pr.current)
            change_pct = (
                ((suggested - pr.current) / pr.current * 100)
                if pr.current != 0
                else 0.0
            )

            # Only suggest if change is meaningful (> 2%)
            if abs(change_pct) < 2.0:
                continue

            # Determine confidence based on improvement magnitude
            improvement = (
                ((best_value - baseline) / abs(baseline) * 100)
                if baseline != 0
                else 0.0
            )
            if abs(improvement) > 20:
                confidence = "high"
            elif abs(improvement) > 5:
                confidence = "medium"
            else:
                confidence = "low"

            reason = (
                f"{pr.description}: change from {pr.current:.4f} to "
                f"{suggested:.4f} ({change_pct:+.1f}%). "
                f"Expected objective improvement: {improvement:+.1f}%."
            )

            suggestions.append(
                ParamSuggestion(
                    param_name=pr.name,
                    current_value=pr.current,
                    suggested_value=round(suggested, 6),
                    expected_improvement_pct=round(improvement, 2),
                    reason=reason,
                    confidence=confidence,
                )
            )

        result = TunerResult(
            suggestions=suggestions,
            best_objective_value=best_value,
            baseline_objective_value=baseline,
            n_trials_completed=len(study.trials),
        )

        logger.info(
            "param_tuner.optimisation_complete",
            baseline=round(baseline, 4),
            best=round(best_value, 4),
            n_trials=len(study.trials),
            n_suggestions=len(suggestions),
        )

        return result
