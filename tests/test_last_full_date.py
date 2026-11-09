import unittest
from datetime import date, timedelta
import polars as pl
from src.features import get_last_full_date, filter_to_last_full_date


class TestLastFullDate(unittest.TestCase):
    def test_normal_trading_days(self):
        """When all days have full data, returns the latest date."""
        df = pl.DataFrame({
            "date": [date(2026, 11, 4)] * 50 + [date(2026, 11, 5)] * 50 + [date(2026, 11, 6)] * 50,
            "symbol": [f"SYM_{i}" for i in range(50)] * 3,
            "close": [100.0] * 150,
        })
        self.assertEqual(get_last_full_date(df), date(2026, 11, 6))

    def test_partial_saturday_ignored(self):
        """When Saturday only has a small subset of tickers, returns Friday."""
        df = pl.DataFrame({
            # Friday has 100 tickers
            "date": [date(2026, 11, 6)] * 100 + [date(2026, 11, 7)] * 3,
            "symbol": [f"SYM_{i}" for i in range(100)] + ["SYM_1", "SYM_2", "SYM_3"],
            "close": [50.0] * 103,
        })
        self.assertEqual(get_last_full_date(df), date(2026, 11, 6))

    def test_multiple_partial_weekend_days_ignored(self):
        """When Saturday, Sunday, and partial Monday morning exist, returns Friday."""
        df = pl.DataFrame({
            "date": (
                [date(2026, 11, 6)] * 100  # Friday (full)
                + [date(2026, 11, 7)] * 3   # Saturday (partial)
                + [date(2026, 11, 8)] * 1   # Sunday (partial)
                + [date(2026, 11, 9)] * 10  # Monday pre-market (partial)
            ),
            "symbol": (
                [f"SYM_{i}" for i in range(100)]
                + ["SYM_1", "SYM_2", "SYM_3"]
                + ["SYM_1"]
                + [f"SYM_{i}" for i in range(10)]
            ),
            "close": [25.0] * 114,
        })
        self.assertEqual(get_last_full_date(df), date(2026, 11, 6))

    def test_monday_transitions_to_full(self):
        """When Monday receives full data after Saturday partial data, returns Monday."""
        df = pl.DataFrame({
            "date": (
                [date(2026, 11, 6)] * 100  # Friday (full)
                + [date(2026, 11, 7)] * 3   # Saturday (partial)
                + [date(2026, 11, 9)] * 102 # Monday (full)
            ),
            "symbol": (
                [f"SYM_{i}" for i in range(100)]
                + ["SYM_1", "SYM_2", "SYM_3"]
                + [f"SYM_{i}" for i in range(102)]
            ),
            "close": [30.0] * 205,
        })
        self.assertEqual(get_last_full_date(df), date(2026, 11, 9))

    def test_etf_scale_universe(self):
        """Dynamic calculation scales accurately to ETF universe sizes (~1600 symbols)."""
        symbols = [f"ETF_{i}" for i in range(1600)]
        df = pl.DataFrame({
            "date": [date(2026, 11, 5)] * 1600 + [date(2026, 11, 6)] * 1600 + [date(2026, 11, 7)] * 2,
            "symbol": symbols + symbols + ["ETF_1", "ETF_2"],
            "close": [45.0] * 3202,
        })
        self.assertEqual(get_last_full_date(df), date(2026, 11, 6))

    def test_small_test_universe(self):
        """Works even on small synthetic test datasets with only a few symbols."""
        df = pl.DataFrame({
            "date": [date(2026, 1, 1)] * 5 + [date(2026, 1, 2)] * 5 + [date(2026, 1, 3)] * 1,
            "symbol": ["A", "B", "C", "D", "E"] + ["A", "B", "C", "D", "E"] + ["A"],
            "close": [10.0] * 11,
        })
        self.assertEqual(get_last_full_date(df), date(2026, 1, 2))

    def test_single_ticker_universe(self):
        """Handles single-symbol datasets without crashing or dropping valid dates."""
        df = pl.DataFrame({
            "date": [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)],
            "symbol": ["AAPL", "AAPL", "AAPL"],
            "close": [150.0, 151.0, 152.0],
        })
        self.assertEqual(get_last_full_date(df), date(2026, 1, 3))

    def test_filter_to_last_full_date(self):
        """Verifies filter_to_last_full_date discards rows after the last full date."""
        df = pl.DataFrame({
            "date": [date(2026, 11, 6)] * 50 + [date(2026, 11, 7)] * 2,
            "symbol": [f"S_{i}" for i in range(50)] + ["S_0", "S_1"],
            "close": [10.0] * 52,
        })
        filtered = filter_to_last_full_date(df)
        self.assertEqual(filtered.select(pl.col("date").max()).item(), date(2026, 11, 6))
        self.assertEqual(len(filtered), 50)

    def test_empty_and_edge_cases(self):
        """Verifies graceful handling of empty, None, and 1-day DataFrames."""
        self.assertIsNone(get_last_full_date(None))
        self.assertIsNone(get_last_full_date(pl.DataFrame()))
        self.assertIsNone(get_last_full_date(pl.DataFrame({"val": [1, 2]})))  # missing date col

        # 1-day DataFrame returns that day
        single = pl.DataFrame({"date": [date(2026, 1, 1)], "symbol": ["AAPL"], "close": [100.0]})
        self.assertEqual(get_last_full_date(single), date(2026, 1, 1))

        # Filter on empty returns empty
        self.assertTrue(filter_to_last_full_date(pl.DataFrame()).is_empty())

    def test_ignores_symbols_with_null_or_zero_close(self):
        """Only symbols with valid close price are counted as full data."""
        # Date 1 has 10 valid tickers
        # Date 2 has 2 valid tickers and 8 null/zero close tickers
        df = pl.DataFrame({
            "date": [date(2026, 1, 1)] * 10 + [date(2026, 1, 2)] * 10,
            "symbol": [f"S_{i}" for i in range(10)] + [f"S_{i}" for i in range(10)],
            "close": [10.0] * 10 + [10.0, 10.0] + [0.0, None, 0.0, None, 0.0, None, 0.0, 0.0],
        })
        self.assertEqual(get_last_full_date(df), date(2026, 1, 1))


if __name__ == "__main__":
    unittest.main()
