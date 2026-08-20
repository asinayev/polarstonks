"""Archive logs and datasets to Google Cloud Storage and generate IBKR trade logs."""

import os
import sys
import datetime
import subprocess
import argparse
from google.cloud import storage

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import OUT_DIR, GCS_BUCKET, IB_DIR


def generate_trades(ib_dir: str = IB_DIR, out_dir: str = OUT_DIR, real: bool = True):
    """Runs view_trades.py to produce trade_logs.json and am_trades.txt."""
    script = os.path.join(ib_dir, "market_activity", "view_trades.py")
    python_bin = os.path.join(ib_dir, ".venv", "bin", "python")
    if not os.path.exists(python_bin):
        python_bin = sys.executable

    if not os.path.exists(script):
        print(f"Warning: {script} not found, skipping trade log generation.")
        return

    for ftype, fname in [("json", "trade_logs.json"), ("txt", "am_trades.txt")]:
        out_path = os.path.join(out_dir, fname)
        cmd = [python_bin, script, "--file_type", ftype, "--out_file", out_path]
        if real:
            cmd.append("--real")
        print(f"Generating {fname}...")
        subprocess.run(cmd, cwd=ib_dir)


def upload_to_gcs(out_dir: str = OUT_DIR, bucket_name: str = GCS_BUCKET, files: Optional[list] = None):
    """Uploads daily logs and data files to Google Cloud Storage."""
    today = datetime.date.today().strftime("%Y-%m-%d")
    clean_bucket = bucket_name.replace("gs://", "").strip("/")
    
    try:
        client = storage.Client()
        bucket = client.bucket(clean_bucket)
    except Exception as e:
        print(f"Error initializing GCS client: {e}")
        return

    files_map = {
        "order_logs.json": f"order_logs/{today}.json",
        "trade_logs.json": f"trade_logs/{today}.json",
        "am_trades.txt": f"am_trades/{today}.txt",
        "all_news.csv": f"news_logs/{today}.csv",
        "all_stocks.csv": f"stocks_logs/{today}.csv",
        "all_etfs.csv": f"etfs_logs/{today}.csv",
    }

    if files:
        files_map = {k: v for k, v in files_map.items() if k in files}

    for local_name, remote_blob in files_map.items():
        local_path = os.path.join(out_dir, local_name)
        if not os.path.exists(local_path):
            local_path = os.path.join("/tmp", local_name)

        if os.path.exists(local_path):
            try:
                print(f"Uploading {local_name} -> gs://{clean_bucket}/{remote_blob}")
                bucket.blob(remote_blob).upload_from_filename(local_path)
            except Exception as e:
                print(f"Failed to upload {local_name}: {e}")
        else:
            print(f"Skipping {local_name} (file not found)")


def upload_order_logs(out_dir: str = OUT_DIR, bucket_name: str = GCS_BUCKET):
    """Uploads only order_logs.json to GCS."""
    upload_to_gcs(out_dir=out_dir, bucket_name=bucket_name, files=["order_logs.json"])


def main():
    parser = argparse.ArgumentParser(description="Generate trade logs and archive daily data to GCS.")
    parser.add_argument("--out_dir", default=OUT_DIR, help="Output directory")
    parser.add_argument("--bucket", default=GCS_BUCKET, help="GCS bucket name")
    parser.add_argument("--ib_dir", default=IB_DIR, help="ib_insync repo directory")
    parser.add_argument("--orders_only", "--only_order_logs", dest="orders_only", action="store_true", help="Upload only order_logs.json")
    parser.add_argument("--skip_trades", action="store_true", help="Skip running view_trades.py")
    parser.add_argument("--paper", dest="real", action="store_false", help="Use paper trading for trade logs")
    args = parser.parse_args()

    if args.orders_only:
        upload_order_logs(out_dir=args.out_dir, bucket_name=args.bucket)
        print("Order logs archive complete.")
        return

    if not args.skip_trades:
        generate_trades(ib_dir=args.ib_dir, out_dir=args.out_dir, real=args.real)

    upload_to_gcs(out_dir=args.out_dir, bucket_name=args.bucket)
    print("Archive complete.")


if __name__ == "__main__":
    main()
