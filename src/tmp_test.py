import polars as pl
import glob
import sys

print("Testing polars lazy read")
try:
    df = pl.read_csv("/home/asinayev/data/2025.csv.gz", n_rows=10)
    print("Columns:", df.columns)
except Exception as e:
    print("Error:", e)
