"""Reference statistics for the evals tests."""

import math
import statistics

INF = math.inf

T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447,
       7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179,
       13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101,
       19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064,
       25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045}


def t95_ref(df):
    if df < 1:
        return INF
    return T95.get(df, 1.96)


def mse_ref(values):
    """Reference (mean, se)."""
    if not values:
        return None, INF
    if len(values) == 1:
        return float(values[0]), INF
    return (statistics.fmean(values),
            statistics.stdev(values) / math.sqrt(len(values)))
