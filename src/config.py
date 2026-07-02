import os
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

POLYGON_KEY = os.environ.get('POLYGONKEY')
OUT_DIR = os.environ.get('out_dir', '/tmp/polarstonks')

if not POLYGON_KEY:
    # Fallback or error? For now, let's warn.
    print("Warning: POLYGONKEY environment variable not set.")

if not os.path.exists(OUT_DIR):
    os.makedirs(OUT_DIR)
