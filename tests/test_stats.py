import math

import pytest
from hypothesis import given, settings, strategies as st

from evals_test_helpers import INF, T95, mse_ref
from evals.stats import ci95, mean_and_se, pool, t95


def test_ST01_t95_table():
    """ST-01: t95(df) returns the exact table value for df 1..29."""
    for df, value in T95.items():
        assert t95(df) == pytest.approx(value, abs=1e-9)


@pytest.mark.parametrize("df", [30, 31, 100, 1000])
def test_ST02_t95_large_df(df):
    """ST-02: t95(df) is 1.96 for df >= 30."""
    assert t95(df) == pytest.approx(1.96, abs=1e-9)


@pytest.mark.parametrize("df", [0, -1, -10])
def test_ST03_t95_below_one(df):
    """ST-03: t95(df) is inf for df < 1."""
    assert t95(df) == INF


def test_ST04_mean_and_se_empty_and_single():
    """ST-04: mean_and_se([]) is (None, inf) and mean_and_se([x]) is (x, inf)."""
    assert mean_and_se([]) == (None, INF)
    mean, se = mean_and_se([3.5])
    assert mean == pytest.approx(3.5) and se == INF


def test_ST05_mean_and_se_values():
    """ST-05: mean_and_se uses the n-1 sample variance: [1,2,3,4] -> (2.5, sqrt((5/3)/4)); constants give se 0."""
    mean, se = mean_and_se([1, 2, 3, 4])
    assert mean == pytest.approx(2.5)
    assert se == pytest.approx(math.sqrt((5 / 3) / 4))
    mean, se = mean_and_se([7.0, 7.0, 7.0])
    assert mean == pytest.approx(7.0) and se == pytest.approx(0.0, abs=1e-12)


def test_ST06_ci95():
    """ST-06: ci95(se, n) = t95(n-1) * se; inf whenever n < 2 (whatever se) or se is inf."""
    assert ci95(1.0, 2) == pytest.approx(12.706)
    assert ci95(0.5, 31) == pytest.approx(0.98)
    assert ci95(2.0, 11) == pytest.approx(2 * 2.228)
    assert ci95(0.0, 5) == pytest.approx(0.0, abs=1e-12)
    assert ci95(INF, 5) == INF
    assert ci95(0.0, 1) == INF
    assert ci95(3.0, 1) == INF
    assert ci95(3.0, 0) == INF
    assert ci95(INF, 1) == INF


def test_ST07_pool_nothing_left():
    """ST-07: pool of nothing, or only ignored runs (n <= 0 or mean None), is (0, None, inf)."""
    assert pool([]) == (0, None, INF)
    assert pool([(0, 5.0, 1.0), (-3, 2.0, 1.0), (4, None, INF)]) == (
        0, None, INF)


def _check(result, values):
    total, mean, se = result
    ref_mean, ref_se = mse_ref(values)
    assert total == len(values)
    assert mean == pytest.approx(ref_mean, rel=1e-9, abs=1e-9)
    if ref_se == INF:
        assert se == INF
    else:
        assert se == pytest.approx(ref_se, rel=1e-6, abs=1e-9)


def test_ST08_pool_combines_batches():
    """ST-08: pool ignores runs with n <= 0 or mean None and equals mean_and_se over all underlying samples."""
    a, b = [1, 2, 3], [10, 20]
    runs = [(3, *mse_ref(a)), (0, 99.0, 1.0), (2, *mse_ref(b)),
            (5, None, INF), (-1, 4.0, 0.5)]
    _check(pool(runs), a + b)


def test_ST09_pool_single_sample_batches():
    """ST-09: n == 1 batches count in the mean and between-batch spread; a total below 2 gives se inf."""
    _check(pool([(1, 4.0, INF), (3, *mse_ref([1, 2, 3]))]), [4, 1, 2, 3])
    _check(pool([(1, 4.0, INF), (1, 8.0, INF), (1, 3.0, INF)]), [4, 8, 3])
    assert pool([(1, 5.0, INF)]) == (1, pytest.approx(5.0), INF)
    assert pool([(1, 5.0, INF), (0, 1.0, 1.0)]) == (1, pytest.approx(5.0),
                                                    INF)


@pytest.mark.parametrize("bad", [INF, math.nan])
def test_ST10_pool_nonfinite_se(bad):
    """ST-10: any batch with n >= 2 and a non-finite se makes the pooled se inf (total and mean still given)."""
    total, mean, se = pool([(3, 2.0, bad), (2, 4.0, 1.0)])
    assert total == 5
    assert mean == pytest.approx(2.8)
    assert se == INF


@settings(max_examples=150, deadline=None)
@given(st.lists(st.lists(st.integers(-1000, 1000).map(lambda x: x / 8),
                         max_size=6), max_size=6))
def test_STP1_pool_matches_concatenation(batches):
    """ST-P1: pool over per-batch (n, mean, se) equals mean_and_se over all samples together."""
    runs = [(len(b), *mse_ref(b)) for b in batches]
    flat = [x for b in batches for x in b]
    result = pool(runs)
    if not flat:
        assert result == (0, None, INF)
    else:
        _check(result, flat)
