import os
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

POLYGON_KEY = os.environ.get('POLYGONKEY')
GOOGLE_KEY = os.environ.get('GOOGLEKEY')
SEARCH_ID = os.environ.get('SEARCHID')
ZACKS_SEARCH_ID = os.environ.get('ZACKSSEARCHID')
OUT_DIR = os.environ.get('out_dir', '/tmp/stonksanalysis')
GCS_BUCKET = os.environ.get('GCS_BUCKET', 'stonksanalysisdatasets')

if not POLYGON_KEY:
    print("Warning: POLYGONKEY environment variable not set.")

def ensure_dir(dir_path):
    """Ensures target directory exists."""
    if not dir_path:
        return
    os.makedirs(dir_path, exist_ok=True)

ensure_dir(OUT_DIR)


