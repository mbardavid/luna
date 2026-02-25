"""Polymarket MM — ai_copilot package.

IA Copilot: assistente de análise, **nunca** execução.

Modules:
    post_mortem      — Daily PnL, fills, spreads, and anomaly analysis.
    param_tuner      — Bayesian optimization of strategy parameters.
    anomaly_detector — Rolling z-score anomaly detection with event_bus alerts.
"""

from .anomaly_detector import AnomalyDetector, AnomalyDetectorConfig
from .param_tuner import ParamTuner, ParamTunerConfig, ParamSuggestion
from .post_mortem import PostMortemAnalyser, DailyReport

__all__ = [
    "AnomalyDetector",
    "AnomalyDetectorConfig",
    "ParamTuner",
    "ParamTunerConfig",
    "ParamSuggestion",
    "PostMortemAnalyser",
    "DailyReport",
]
