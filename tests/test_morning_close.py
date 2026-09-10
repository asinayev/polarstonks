import unittest
from unittest.mock import MagicMock, patch
from ib_async import Stock, Order

from src.execution.order_builder import build_close_order
from src.execution.morning_close import load_close_types, determine_liquidity


class TestMorningCloseLogic(unittest.TestCase):

    def test_build_close_order_mkt_eod_liquid_long(self):
        order = build_close_order(
            account="U123456",
            position_qty=100,
            closetype="mkt_eod",
            illiquid=False,
        )
        self.assertIsNotNone(order)
        self.assertEqual(order.action, "SELL")
        self.assertEqual(order.orderType, "MOC")
        self.assertEqual(order.totalQuantity, 100)
        self.assertEqual(order.account, "U123456")

    def test_build_close_order_mkt_eod_liquid_short(self):
        order = build_close_order(
            account="U123456",
            position_qty=-50,
            closetype="mkt_eod",
            illiquid=False,
        )
        self.assertIsNotNone(order)
        self.assertEqual(order.action, "BUY")
        self.assertEqual(order.orderType, "MOC")
        self.assertEqual(order.totalQuantity, 50)

    def test_build_close_order_mkt_eod_illiquid(self):
        order = build_close_order(
            account="U123456",
            position_qty=200,
            closetype="mkt_eod",
            illiquid=True,
        )
        self.assertIsNotNone(order)
        self.assertEqual(order.action, "SELL")
        self.assertEqual(order.orderType, "MKT")
        self.assertEqual(order.algoStrategy, "Adaptive")
        self.assertEqual(order.totalQuantity, 200)
        self.assertTrue(len(order.conditions) >= 1)

    def test_load_close_types(self):
        with patch("builtins.open", unittest.mock.mock_open(read_data="strategy,close_type\nstrat_a,mkt_eod\nstrat_b,LAST_HIGH_EOD\n")):
            with patch("os.path.exists", return_value=True):
                ctypes = load_close_types("dummy_path.csv")
                self.assertEqual(ctypes.get("strat_a"), "mkt_eod")
                self.assertEqual(ctypes.get("strat_b"), "last_high_eod")
                self.assertEqual(ctypes.get("STRAT_A"), "mkt_eod")

    def test_determine_liquidity_explicit(self):
        current_status = {}
        self.assertTrue(determine_liquidity("AAPL", {"liquid": "0"}, current_status))
        self.assertFalse(determine_liquidity("AAPL", {"liquid": "1"}, current_status))

    def test_determine_liquidity_auto_detect(self):
        current_status = {
            "LIQ": {"volume": "250000"},
            "ILLIQ": {"volume": "50000"},
            "EMPTY": {"volume": ""},
        }
        self.assertFalse(determine_liquidity("LIQ", {}, current_status))
        self.assertTrue(determine_liquidity("ILLIQ", {}, current_status))
        # Fallback to liquid
        self.assertFalse(determine_liquidity("EMPTY", {}, current_status))
        self.assertFalse(determine_liquidity("UNKNOWN", {}, current_status))


if __name__ == "__main__":
    unittest.main()
