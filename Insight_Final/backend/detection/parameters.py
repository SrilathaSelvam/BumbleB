"""Tunable detector parameters.

| Param                | Default | Meaning                                          |
|----------------------|---------|--------------------------------------------------|
| ewma_alpha           | 2/61    | EWMA smoothing (~60s span)                       |
| warmup_snapshots     | 45      | snapshots (1 Hz) before alerts/baseline exposed  |
| min_lines            | 100     | min total_lines in window, else snapshot ignored |
| error_floor          | 0.001   | min absolute error_rate to count as a breach     |
| std_floor            | 0.005   | minimum std used in z-score                      |
| upper_k              | 3.0     | baseline_upper = mean + k*std                    |
| persistence          | 2       | consecutive breaches required to alert           |
| cooldown_s           | 30      | suppress same/lower severity after an alert      |
| warning/high/critical_z | 3/5/8 | severity thresholds                             |
"""
from dataclasses import dataclass


@dataclass
class Params:
    ewma_alpha: float = 2 / 61
    warmup_snapshots: int = 45
    min_lines: int = 100
    error_floor: float = 0.001
    std_floor: float = 0.005
    upper_k: float = 3.0
    persistence: int = 2
    cooldown_s: float = 30.0
    warning_z: float = 3.0
    high_z: float = 5.0
    critical_z: float = 8.0
