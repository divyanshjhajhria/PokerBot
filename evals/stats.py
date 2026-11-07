"""Small statistics helpers for match results (no numpy needed)."""

import math

# Two-sided 95% Student-t critical values for 1..29 degrees of freedom.
_T95 = [
    12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
    2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
    2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045,
]


def t95(degrees_of_freedom: int) -> float:
    """Two-sided 95% Student-t critical value; 1.96 from 30 degrees of
    freedom on, inf below 1."""
    if degrees_of_freedom < 1:
        return math.inf
    if degrees_of_freedom >= 30:
        return 1.96
    return _T95[degrees_of_freedom - 1]


def mean_and_se(values: list[float]) -> tuple[float | None, float]:
    """(mean, standard error of the mean); (None, inf) for no values and
    (value, inf) for one."""
    count = len(values)
    if count == 0:
        return None, math.inf
    mean = sum(values) / count
    if count == 1:
        return mean, math.inf
    variance = sum((v - mean) ** 2 for v in values) / (count - 1)
    return mean, math.sqrt(variance / count)


def ci95(standard_error: float, samples: int) -> float:
    """Half-width of the 95% confidence interval for a mean of samples."""
    if samples < 2:
        return math.inf
    return t95(samples - 1) * standard_error


def pool(
    runs: list[tuple[int, float | None, float]],
) -> tuple[int, float | None, float]:
    """Combines (samples, mean, standard error) runs of the same quantity
    into one, as if all samples had been measured together."""
    runs = [run for run in runs if run[0] > 0 and run[1] is not None]
    total = sum(n for n, _, _ in runs)
    if total == 0:
        return 0, None, math.inf
    mean = sum(n * m for n, m, _ in runs) / total
    if total < 2:
        return total, mean, math.inf
    # Squared deviations from the pooled mean: each run's own spread
    # (se^2 = variance / n, variance over n - 1) plus its offset.
    squares = 0.0
    for n, run_mean, se in runs:
        if n >= 2:
            if not math.isfinite(se):
                return total, mean, math.inf
            squares += se * se * n * (n - 1)
        squares += n * (run_mean - mean) ** 2
    return total, mean, math.sqrt(squares / (total - 1) / total)
