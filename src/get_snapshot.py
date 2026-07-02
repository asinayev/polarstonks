import polars as pl
from datetime import date, datetime, timedelta
import os
import sys
import logging

# Add parent dir to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import POLYGON_KEY
from src.polygon_client import PolygonClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def main():
    if not POLYGON_KEY:
        logger.error("POLYGONKEY not set.")
        sys.exit(1)
        
    client = PolygonClient(POLYGON_KEY)
    
    try:
        logger.info("Attempting to hit Polygon API for snapshot...")
        current_moves_df = client.get_snapshot_all_tickers(market='stocks', locale='us')
        
        if current_moves_df.is_empty():
            logger.error("Empty snapshot received.")
            sys.exit(1)
            
        logger.info(f"API response received with {len(current_moves_df)} tickers, processing...")
        
        # R script logic:
        # current_moves_dt = current_moves_dt[,
        #                               .(symbol=ticker,date=Sys.Date()-1, AdjClose=prevDay.c, 
        #                                  open=prevDay.o, high=prevDay.h, low=prevDay.l, 
        #                                  volume=prevDay.v, close=prevDay.c, overnight_delta=1+todaysChangePerc/100,
        #                                 updated=as.integer(updated/10^9)
        #                                  )]
        
        yesterday = date.today() - timedelta(days=1)
        
        current_moves_dt = current_moves_df.select([
            pl.col("ticker").alias("symbol"),
            pl.lit(yesterday).alias("date"),
            pl.col("prevDay.c").alias("AdjClose"),
            pl.col("prevDay.o").alias("open"),
            pl.col("prevDay.h").alias("high"),
            pl.col("prevDay.l").alias("low"),
            pl.col("prevDay.v").alias("volume"),
            pl.col("prevDay.c").alias("close"),
            (1 + pl.col("todaysChangePerc") / 100).alias("overnight_delta"),
            (pl.col("updated") / 1e9).cast(pl.Int64).alias("updated")
        ])
        
        logger.info("Writing to /tmp/stonksanalysis/current_moves.csv...")
        current_moves_dt.write_csv('/tmp/stonksanalysis/current_moves.csv')
        
        # Also save SPY change to /tmp/stonksanalysis/spy_change.csv as nightmoves.R does
        spy_change = current_moves_df.filter(pl.col("ticker") == "SPY")
        if not spy_change.is_empty():
            logger.info("Writing SPY change to /tmp/stonksanalysis/spy_change.csv...")
            spy_change.write_csv('/tmp/stonksanalysis/spy_change.csv')
            
        logger.info("Done!")
        
    except Exception as e:
        logger.error(f"Error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
