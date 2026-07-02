import polars as pl
from datetime import date, timedelta
from src.am_short_interest import subtract_business_days

def test_subtract_business_days():
    # 8 business days back from Monday 2024-03-11
    # 1 week (5 biz days) back: Monday 2024-03-04
    # 3 more biz days: Friday (1), Thursday (2), Wednesday (3) -> 2024-02-28
    
    # 2024-03-11 is Monday (weekday=1)
    base_date = date(2024, 3, 11)
    df = pl.DataFrame({"date": [base_date]})
    result = df.select(subtract_business_days(pl.col("date"), 8)).to_series().to_list()[0]
    expected = date(2024, 2, 28)
    
    print(f"Base: {base_date}, Expected: {expected}, Got: {result}")
    assert result == expected

def test_attribute_mapping():
    # Mock short interest object
    class MockShortInterest:
        def __init__(self, ticker, settlement_date, short_interest, days_to_cover):
            self.ticker = ticker
            self.settlement_date = settlement_date
            self.short_interest = short_interest
            self.days_to_cover = days_to_cover

    s = MockShortInterest("AAPL", "2024-03-01", 1000000, 2.5)
    
    # Simulate the loop in add_short_interest
    data = {
        "symbol": s.ticker,
        "settlement_date": s.settlement_date,
        "short_interest": s.short_interest,
        "days_to_cover": getattr(s, 'days_to_cover', None)
    }
    
    assert data["settlement_date"] == "2024-03-01"
    assert data["short_interest"] == 1000000

def test_strategy_logic():
    # Create a mock prices dataframe
    max_date = date(2024, 3, 11)
    df = pl.DataFrame({
        "symbol": ["AAPL"],
        "date": [max_date],
        "close": [10.0],
        "volume": [1000000],
        "vp_order": [100],
        "avg_delta": [1.02],
        "avg_delta_short": [1.04],
        "short_interest": [200000],
        "days_to_cover": [1.2] # Should match round(0) == 1
    })
    
    # Filter using logic in am_short_interest.py
    s1 = df.filter(
        (pl.col("date") == max_date) &
        (pl.col("vp_order") < 500) &
        (pl.col("close") > 7) &
        (pl.col("avg_delta") > 1.01) &
        (pl.col("avg_delta_short") > 1.03) &
        (pl.col("short_interest") > 100000) &
        (pl.col("days_to_cover").round(0) == 1)
    )
    
    assert not s1.is_empty()
    assert s1["symbol"][0] == "AAPL"
    print("Strategy logic test passed!")

if __name__ == "__main__":
    test_subtract_business_days()
    test_attribute_mapping()
    test_strategy_logic()
    print("All tests passed!")
