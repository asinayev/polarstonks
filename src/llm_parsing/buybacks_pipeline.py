"""Batch buybacks and guidance search pipeline generating enriched CSV datasets."""

import os
import sys
import logging
from google import genai
from google.genai import types

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.config import GOOGLE_KEY, POLYGON_KEY, SEARCH_ID, OUT_DIR
from src.llm_parsing.search_pipeline import read_search

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def run_buybacks_pipeline():
    """Runs the buybacks and guidance extraction pipelines."""
    if not GOOGLE_KEY or not POLYGON_KEY or not SEARCH_ID:
        logger.error("Missing GOOGLEKEY, POLYGONKEY, or SEARCHID in environment or .env file.")
        return

    client = genai.Client(api_key=GOOGLE_KEY)
    model_id = "gemini-3.5-flash-lite"

    safety_settings = [
        types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
        types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
        types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
        types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
    ]

    config = types.GenerateContentConfig(
        safety_settings=safety_settings,
        response_mime_type="application/json",
    )

    prompt_template = """
Determine if the following search result includes a NEW announcement about a {}  Answer 'yes' or 'no'.

For your response, also extract (or infer) the following:
- Company Full Name
- Stock Ticker Symbol
- Publication Date and Time
- A short quote from the search result that supports your 'yes'/'no' determination.

1. Locate the title. If the title doesn't refer to the company at all, RESPOND NO (not a new program).  
2. Locate the publication time in the search result's snippet, body or metadata, also try searching inside the URL for the date. Reformat to YYYY-MM-DD HH:MM using military time.
3. Read the rest of the announcement. Pay special attention to the link and title.
4. If the explicit ticker symbol is omitted from the snippet text, use your knowledge of public company listings to infer the official primary stock exchange ticker symbol for the company name (e.g. 'SSR Mining' -> 'SSRM', 'TotalEnergies' -> 'TTE', 'Pershing Square' -> 'PSH'). Only output 'UNKNOWN' if the company is strictly private or unlisted.

Respond in JSON format as shown in this example:
{{"newProgram":"No","companyName":"Microsoft Corporation","ticker":"MSFT","timePublished":"2024-08-12 15:30","quote":"In June, the board announced plans for a new shareholder incentive..."}}

Search result:
"""

    # --- BUYBACKS ---
    buyback_desc = (
        "an announcement of a share buyback or share repurchase program. "
        "If the announcement does not say that stock will be repurchased or bought back IN THE FUTURE, respond no. "
        "Also, if the buyback is funded by or part of a debt/note issuance, or is specifically meant to offset some "
        "other corporate financial action (such as dilution from option exercises or stock-based compensation), respond no."
    )
    buyback_params = {
        "search_query": "buyback|repurchase share|stock",
        "prompt_template": prompt_template.format(buyback_desc),
        "min_close": 5,
        "min_volume": 20000,
        "max_market_cap": 10000000000,
        "min_overnight_pchange": -2.2,
        "max_overnight_pchange": 9,
    }

    read_search(
        google_key=GOOGLE_KEY,
        polygon_key=POLYGON_KEY,
        search_id=SEARCH_ID,
        client=client,
        model_id=model_id,
        config=config,
        write_to_dir=OUT_DIR,
        parameters=buyback_params,
    )

    # --- GUIDANCE ---
    guidance_desc = (
        "improved guidance in the newProgram field. The announcement should say that guidance for an important "
        "metric is now higher (better) than previously expected. If the announcement is simply an update about "
        "progress on existing guidance, respond no."
    )
    guidance_params = {
        "search_query": "improves|raises|increases guidance|outlook",
        "prompt_template": prompt_template.format(guidance_desc),
        "min_close": 5,
        "min_volume": 20000,
        "max_market_cap": 100000000000,
        "min_overnight_pchange": -0.2,
        "max_overnight_pchange": 20,
    }

    read_search(
        google_key=GOOGLE_KEY,
        polygon_key=POLYGON_KEY,
        search_id=SEARCH_ID,
        client=client,
        model_id=model_id,
        config=config,
        write_to_dir=OUT_DIR,
        parameters=guidance_params,
    )


def main():
    run_buybacks_pipeline()


if __name__ == "__main__":
    main()
