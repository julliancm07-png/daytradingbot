from __future__ import annotations

from typing import Union

from daytradingbot.core.config import StrategySettings
from daytradingbot.strategy.baseline import RulesBaselineStrategy
from daytradingbot.strategy.mean_reversion import MeanReversionStrategy

StrategyType = Union[RulesBaselineStrategy, MeanReversionStrategy]


def create_strategy(cfg: StrategySettings) -> StrategyType:
    if cfg.strategy_type == "mean_reversion":
        return MeanReversionStrategy(cfg)
    return RulesBaselineStrategy(cfg)
