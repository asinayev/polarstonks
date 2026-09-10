import unittest
from unittest.mock import MagicMock, patch
from ib_async import Stock, Order, PriceCondition, TimeCondition
import argparse

from src.config import MIN_PRICE
from src.execution.morning_orders import _parse_condition_price, place_order, DEFAULT_MIN_PRICE


class TestMorningOrdersPriceLimits(unittest.TestCase):

    def test_default_minprice_from_config(self):
        self.assertEqual(DEFAULT_MIN_PRICE, MIN_PRICE)
        self.assertEqual(DEFAULT_MIN_PRICE, 6.0)

    def test_parse_condition_price(self):
        self.assertIsNone(_parse_condition_price(None))
        self.assertIsNone(_parse_condition_price(""))
        self.assertIsNone(_parse_condition_price("   "))
        self.assertIsNone(_parse_condition_price("nan"))
        self.assertIsNone(_parse_condition_price("None"))
        self.assertIsNone(_parse_condition_price("invalid"))
        self.assertIsNone(_parse_condition_price(-5.0))
        self.assertEqual(_parse_condition_price("12.50"), 12.50)
        self.assertEqual(_parse_condition_price(25.75), 25.75)

    @patch("src.execution.morning_orders.get_stock_price")
    @patch("src.execution.morning_orders.qualify_stock_contract")
    @patch("src.execution.morning_orders.log_trade")
    def test_price_condition_min_attached(self, mock_log, mock_qualify, mock_price):
        mock_ib = MagicMock()
        mock_contract = Stock("AAPL", "SMART", "USD", conId=12345)
        mock_qualify.return_value = mock_contract
        mock_price.return_value = 100.0

        args = argparse.Namespace(minprice=6.0, account="U123456", logfile="dummy.json")
        row = {
            "symbol": "AAPL",
            "action": "BUY",
            "order_type": "Adaptive",
            "time_in_force": "DAY",
            "strike_price": "100.0",
            "price_condition_min": "101.5",
        }

        place_order(row, mock_ib, cash=1000.0, strat_file="div_news.csv", args=args)

        mock_ib.placeOrder.assert_called_once()
        placed_contract, placed_order = mock_ib.placeOrder.call_args[0]

        self.assertEqual(placed_contract.symbol, "AAPL")
        self.assertEqual(placed_order.totalQuantity, 10)  # round(1000 / 100)
        self.assertEqual(len(placed_order.conditions), 2)

        # First condition: TimeCondition before 09:35:00 US/Eastern
        time_cond = placed_order.conditions[0]
        self.assertIsInstance(time_cond, TimeCondition)
        self.assertFalse(time_cond.isMore)
        self.assertIn("09:35:00 US/Eastern", time_cond.time)

        # Second condition: PriceCondition min (isMore=True)
        price_cond = placed_order.conditions[1]
        self.assertIsInstance(price_cond, PriceCondition)
        self.assertTrue(price_cond.isMore)
        self.assertEqual(price_cond.price, 101.5)
        self.assertEqual(price_cond.conId, 12345)

    @patch("src.execution.morning_orders.get_stock_price")
    @patch("src.execution.morning_orders.qualify_stock_contract")
    @patch("src.execution.morning_orders.log_trade")
    def test_price_condition_max_attached(self, mock_log, mock_qualify, mock_price):
        mock_ib = MagicMock()
        mock_contract = Stock("MSFT", "SMART", "USD", conId=67890)
        mock_qualify.return_value = mock_contract
        mock_price.return_value = 200.0

        args = argparse.Namespace(minprice=6.0, account="U123456", logfile="dummy.json")
        row = {
            "symbol": "MSFT",
            "action": "BUY",
            "order_type": "MKT",
            "time_in_force": "DAY",
            "strike_price": "200.0",
            "price_condition_max": "198.0",
        }

        place_order(row, mock_ib, cash=2000.0, strat_file="news_strat.csv", args=args)

        mock_ib.placeOrder.assert_called_once()
        _, placed_order = mock_ib.placeOrder.call_args[0]

        self.assertEqual(len(placed_order.conditions), 2)
        price_cond = placed_order.conditions[1]
        self.assertIsInstance(price_cond, PriceCondition)
        self.assertFalse(price_cond.isMore)
        self.assertEqual(price_cond.price, 198.0)
        self.assertEqual(price_cond.conId, 67890)

    @patch("src.execution.morning_orders.get_stock_price")
    @patch("src.execution.morning_orders.qualify_stock_contract")
    @patch("src.execution.morning_orders.log_trade")
    def test_both_price_conditions_attached(self, mock_log, mock_qualify, mock_price):
        mock_ib = MagicMock()
        mock_contract = Stock("GOOG", "SMART", "USD", conId=11111)
        mock_qualify.return_value = mock_contract
        mock_price.return_value = 50.0

        args = argparse.Namespace(minprice=6.0, account="U123456", logfile="dummy.json")
        row = {
            "symbol": "GOOG",
            "action": "BUY",
            "order_type": "Adaptive",
            "time_in_force": "DAY",
            "strike_price": "50.0",
            "price_condition_min": "48.0",
            "price_condition_max": "52.0",
        }

        place_order(row, mock_ib, cash=500.0, strat_file="corridor.csv", args=args)

        mock_ib.placeOrder.assert_called_once()
        _, placed_order = mock_ib.placeOrder.call_args[0]

        self.assertEqual(len(placed_order.conditions), 3)
        self.assertIsInstance(placed_order.conditions[0], TimeCondition)
        self.assertTrue(placed_order.conditions[1].isMore)
        self.assertEqual(placed_order.conditions[1].price, 48.0)
        self.assertFalse(placed_order.conditions[2].isMore)
        self.assertEqual(placed_order.conditions[2].price, 52.0)

    @patch("src.execution.morning_orders.get_stock_price")
    @patch("src.execution.morning_orders.qualify_stock_contract")
    def test_safety_check_minprice_skips(self, mock_qualify, mock_price):
        mock_ib = MagicMock()
        mock_qualify.return_value = Stock("PENNY", "SMART", "USD", conId=999)
        mock_price.return_value = 4.50  # below default minprice 6.0

        args = argparse.Namespace(minprice=6.0, account="U123456", logfile="dummy.json")
        row = {"symbol": "PENNY", "action": "BUY", "order_type": "MKT", "strike_price": "4.50"}

        place_order(row, mock_ib, cash=100.0, strat_file="strat.csv", args=args)
        mock_ib.placeOrder.assert_not_called()

    @patch("src.execution.morning_orders.get_stock_price")
    @patch("src.execution.morning_orders.qualify_stock_contract")
    def test_safety_check_cash_mult_skips(self, mock_qualify, mock_price):
        mock_ib = MagicMock()
        mock_qualify.return_value = Stock("EXP", "SMART", "USD", conId=888)
        mock_price.return_value = 160.0

        args = argparse.Namespace(minprice=6.0, account="U123456", logfile="dummy.json")
        # cash = 100, round(100 / 160) = 1. 160 * 1 = 160 >= 100 * 1.5 (150) -> should skip
        row = {"symbol": "EXP", "action": "BUY", "order_type": "MKT", "strike_price": "160.0"}

        place_order(row, mock_ib, cash=100.0, strat_file="strat.csv", args=args)
        mock_ib.placeOrder.assert_not_called()


if __name__ == "__main__":
    unittest.main()
