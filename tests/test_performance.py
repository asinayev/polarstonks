import polars as pl
import pytest
from datetime import date
from research.performance import calculate_drawdown, evaluate_performance

def test_calculate_drawdown():
    # Simple case
    outcomes = pl.Series([0.1, -0.05, -0.1, 0.2, -0.3, 0.1])
    # Expected:
    # 0.1 -> 0
    # -0.05 -> -0.05
    # -0.1 -> -0.15
    # 0.2 -> 0.05 (capped at 0 in this path logic? No, R logic is: if((.x+.y)>0){0} else {.x+.y})
    # 0.1 -> 0.0
    # -0.05 -> -0.05
    # -0.1 -> -0.15
    # 0.2 -> -0.15 + 0.2 = 0.05 -> 0.0
    # -0.3 -> 0.0 + -0.3 = -0.3
    # 0.1 -> -0.3 + 0.1 = -0.2
    
    expected = [0.0, -0.05, -0.15, 0.0, -0.3, -0.2]
    result = calculate_drawdown(outcomes).to_list()
    assert result == pytest.approx(expected)

def test_evaluate_performance_basic():
    # Mock data: 3 days, 1 trade each day
    df = pl.DataFrame({
        "date": [date(2023, 1, 1), date(2023, 1, 2), date(2023, 1, 3)],
        "outcome": [0.01, -0.02, 0.03],
        "symbol": ["AAPL", "MSFT", "GOOG"],
        "sell_date": [date(2023, 1, 2), date(2023, 1, 3), date(2023, 1, 4)]
    })
    
    daily = evaluate_performance(df, hold_max=1)
    
    # 2023-01-01: Buy AAPL. n_held=1.
    # 2023-01-02: AAPL sold. Buy MSFT. n_held=1.
    # 2023-01-03: MSFT sold. Buy GOOG. n_held=1.
    
    assert len(daily) >= 3
    assert daily.filter(pl.col("date") == date(2023, 1, 1))["outcome"][0] == 0.01
    assert daily.filter(pl.col("date") == date(2023, 1, 1))["n_held"][0] == 1
    # On day 2, AAPL is sold. n_held = 1 + (0-1) = 0.
    # Note: Even if n_held=0, the slot was blocked for buying MSFT.
    assert daily.filter(pl.col("date") == date(2023, 1, 2))["n_held"][0] == 0
    
def test_evaluate_performance_constraints():
    # 5 trades on day 1
    df = pl.DataFrame({
        "date": [date(2023, 1, 1)] * 5,
        "outcome": [0.01] * 5,
        "symbol": ["S1", "S2", "S3", "S4", "S5"],
        "sell_date": [date(2023, 1, 5)] * 5
    })
    
    daily_1 = evaluate_performance(df, buy_per_day_max=1)
    # Only 1 trade bought. outcome sum = 0.01.
    assert daily_1.filter(pl.col("date") == date(2023, 1, 1))["outcome"][0] == pytest.approx(0.01)
    assert daily_1.filter(pl.col("date") == date(2023, 1, 1))["trades"][0] == 1
    
    # Test hold_max=5
    daily_5 = evaluate_performance(df, hold_max=5, buy_per_day_max=5)
    # 5 trades bought. outcome sum = 0.05.
    assert daily_5.filter(pl.col("date") == date(2023, 1, 1))["outcome"][0] == pytest.approx(0.05)
    assert daily_5.filter(pl.col("date") == date(2023, 1, 1))["trades"][0] == 5

def test_evaluate_performance_sorting():
    # Case where a sell_date has no corresponding trade dates in the results
    # This caused a null in the join and mis-sorting if handled incorrectly
    df = pl.DataFrame({
        "date": [date(2023, 1, 10), date(2023, 1, 1)],
        "outcome": [0.01, 0.02],
        "symbol": ["S1", "S2"],
        "sell_date": [date(2023, 1, 15), date(2023, 1, 5)]
    })
    
    # After join, we'll have dates: Jan 1, Jan 5 (sell-only), Jan 10, Jan 15 (sell-only)
    # n_held Jan 1: 1 (buy S2)
    # n_held Jan 5: 0 (sell S2)
    # n_held Jan 10: 1 (buy S1)
    # n_held Jan 15: 0 (sell S1)
    
    daily = evaluate_performance(df)
    assert daily["date"].to_list() == [date(2023, 1, 1), date(2023, 1, 5), date(2023, 1, 10), date(2023, 1, 15)]
    assert daily["n_held"].to_list() == [1, 0, 1, 0]
    assert (daily["n_held"] >= 0).all()
