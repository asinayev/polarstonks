import polars as pl
from polygon import RESTClient
from datetime import date, datetime, timedelta
import logging
import os
import time

logger = logging.getLogger(__name__)

import urllib3

class PolygonClient:
    def __init__(self, api_key):
        if not api_key:
            raise ValueError("API Key is required")
        self.client = RESTClient(api_key)
        
        # Increase connection pool size to prevent urllib3 warnings during multithreading
        # The polygon SDK uses urllib3.PoolManager directly, not requests.Session
        if hasattr(self.client, 'client') and isinstance(self.client.client, urllib3.PoolManager):
            self.client.client = urllib3.PoolManager(num_pools=10, maxsize=20)

    def get_stock_list(self, date_active=None, market='stocks', type='CS', limit=1000):
        """Fetches active tickers."""
        tickers_data = []
        try:
            # Pass date if provided
            kwargs = {
                'market': market,
                'type': type,
                'limit': limit,
                'active': True 
            }
            if date_active:
                kwargs['date'] = date_active
            
            logger.info(f"Calling list_tickers with kwargs: {kwargs}")
            count = 0
            for t in self.client.list_tickers(**kwargs):
                tickers_data.append({
                    'symbol': t.ticker,
                    'name': t.name,
                    'market': t.market,
                    'locale': t.locale,
                    'type': t.type,
                    'currency_name': t.currency_name,
                    'active': t.active,
                })
                count += 1
                if count % 1000 == 0:
                    logger.info(f"Fetched {count} tickers...")
            
            logger.info(f"Total tickers fetched from API: {count}")
        except Exception as e:
            logger.error(f"Error fetching stock list: {e}")
            return pl.DataFrame() # Return empty on failure
        
        return pl.DataFrame(tickers_data)

    def get_snapshot_all_tickers(self, market='stocks', locale='us'):
        """Fetches snapshot for all tickers in a market."""
        snapshots = []
        try:
            # client.get_snapshot_all(market_type)
            # Returns a list of TickerSnapshot objects
            for s in self.client.get_snapshot_all(market_type=market):
                snapshots.append({
                    'ticker': s.ticker,
                    'todaysChangePerc': getattr(s, 'todays_change_percent', 0),
                    'updated': s.updated,
                    'prevDay.c': getattr(s.prev_day, 'close', None),
                    'prevDay.o': getattr(s.prev_day, 'open', None),
                    'prevDay.h': getattr(s.prev_day, 'high', None),
                    'prevDay.l': getattr(s.prev_day, 'low', None),
                    'prevDay.v': getattr(s.prev_day, 'volume', None),
                })
        except Exception as e:
            logger.error(f"Error fetching snapshot: {e}")
            return pl.DataFrame()
            
        return pl.DataFrame(snapshots)

    def get_snapshot_ticker(self, ticker, market='stocks'):
        """Fetches snapshot for a single ticker."""
        try:
            return self.client.get_snapshot_ticker(market_type=market, ticker=ticker)
        except Exception as e:
            logger.error(f"Error fetching snapshot for {ticker}: {e}")
            return None
        
    def get_ticker_details(self, ticker, date_val=None):
        """Fetches details including market cap."""
        try:
            # client.get_ticker_details(ticker, date=...)
            details = self.client.get_ticker_details(ticker, date=date_val)
            return {
                'symbol': details.ticker,
                'market_cap': getattr(details, 'market_cap', None),
                'shares_outstanding': getattr(details, 'weighted_shares_outstanding', None) 
                                      or getattr(details, 'share_class_shares_outstanding', None)
            }
        except Exception as e:
            # logger.error(f"Error fetching details for {ticker}: {e}")
            return None

    def get_history(self, ticker, start_date, end_date, multiplier=1, timespan='day', adjusted=True):
        """Fetches aggregate bars."""
        aggs = []
        try:
            # Convert dates to YYYY-MM-DD string if passed as date objects
            s_date = start_date.strftime('%Y-%m-%d') if isinstance(start_date, (date, datetime)) else start_date
            e_date = end_date.strftime('%Y-%m-%d') if isinstance(end_date, (date, datetime)) else end_date

            for a in self.client.list_aggs(ticker, multiplier, timespan, s_date, e_date, adjusted=adjusted, limit=50000):
                aggs.append({
                    'symbol': ticker,
                    # timestamp is in ms, convert to date
                    'date': date.fromtimestamp(a.timestamp / 1000), 
                    'open': a.open,
                    'high': a.high,
                    'low': a.low,
                    'close': a.close,
                    'volume': a.volume,
                    'vwap': a.vwap,
                    'transactions': a.transactions
                })
        except Exception as e:
            logger.error(f"Error fetching history for {ticker}: {e}")
            return pl.DataFrame()

        return pl.DataFrame(aggs)

# Example usage/helper
def get_data_for_tickers(client, tickers, start_date, end_date, max_workers=10):
    # In a real script, use ThreadPoolExecutor or similar, but for now sequential or simple loop
    # Polars is fast but API calls are I/O bound.
    # We'll rely on the main script to handle concurrency if needed.
    pass
