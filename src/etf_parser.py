"""ETF classification and leverage extraction using Gemini LLM."""

import os
import sys
import csv
import json
import logging
import time
from typing import List, Dict, Any, Optional
from google import genai

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import GOOGLE_KEY, OUT_DIR

# Configure logging
log_file_path = os.path.join(OUT_DIR, "etf_analysis.log")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

INPUT_CSV_FILE = os.path.join(OUT_DIR, "etf_list.csv")
OUTPUT_CSV_FILE = os.path.join(OUT_DIR, "etf_analysis.csv")
TICKER_COLUMN_NAME = "symbol"
NAME_COLUMN_NAME = "name"
MODEL_NAME = "gemini-2.5-flash"

PROMPT_TEMPLATE = """
You are an expert financial analyst. Analyze the ETF provided below.
Use both the ticker and the name to make an accurate assessment.

ETF Ticker: {etf_ticker}
ETF Name/Description: {etf_name}

Please provide your analysis strictly in a single, valid JSON object. Do not include any text or markdown formatting (like ```json) before or after the JSON object.

The JSON object must contain the following keys:

**1. category:** Classify the ETF into ONE of the following, based on the asset(s) it tracks (regardless of how):
'equity basket' -- tracks price of combination of companies that produce goods or services, including indirectly (like swaps) 
'single equity' -- tracks a single equity, including indirectly (like swaps) 
'debt' -- includes derivatives that track debt like leveraged treasuries
'physical commodities' -- tracks commodities like gold or oil, including indirectly (like gold miners)
'cryptocurrency' -- tracks cryptocurrencies including, including indirectly (like MARA Holdings or Strategy Inc)
'risky strategies' -- financial derivatives intended only to hedge or for speculation (like volatility, buying puts, credit default swaps etc.) and excluding derivatives for tracking the other categories above
'safe strategies' -- financial derivatives intended to be safe or produce income (like collared trades, income) and excluding derivatives for tracking the other categories above
'other'

**2. leverage:** Specify the leverage factor as a number.
Use 1 for no leverage.
Use positive numbers for long leverage (e.g., 2 for 2x, 3 for 3x).
Use negative numbers for inverse leverage (e.g., -1 for -1x, -2 for -2x).
Use 1.5, 2.5, etc., for fractional leverage.

---
**EXAMPLES**
---

**Example 1:**
* ETF Ticker: TQQQ
* ETF Name/Description: ProShares UltraPro QQQ
* JSON Output:
{{
  "category": "equity basket",
  "leverage": 3
}}

**Example 2:**
* ETF Ticker: VXX
* ETF Name/Description: iPath Series B S&P 500 VIX Short-Term Futures ETN
* JSON Output:
{{
  "category": "risky strategies",
  "leverage": 1
}}

---
**TASK**
---

Now, analyze the following ETF:
ETF Ticker: {etf_ticker}
ETF Name/Description: {etf_name}
JSON Output:
"""


def read_input_csv(filename: str, ticker_column: str, name_column: str) -> Optional[List[Dict[str, Any]]]:
    """Reads the input CSV and returns a list of dictionaries."""
    etfs = []
    try:
        with open(filename, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames

            if not fieldnames:
                logger.error(f"Could not read fieldnames from {filename}.")
                return None

            if ticker_column not in fieldnames:
                logger.error(f"Column '{ticker_column}' not found in {filename}. Available: {fieldnames}")
                return None

            for row in reader:
                etfs.append(row)
        logger.info(f"Successfully read {len(etfs)} ETFs from {filename}.")
        return etfs
    except Exception as e:
        logger.error(f"Error reading CSV {filename}: {e}")
        return None


def create_prompt(etf_data: Dict[str, Any], ticker_column: str, name_column: str) -> Tuple[Optional[str], str]:
    """Create a prompt from the template and ETF data."""
    ticker = etf_data.get(ticker_column)
    name = etf_data.get(name_column, "")

    if not ticker:
        return None, "No ticker found in row"

    prompt = PROMPT_TEMPLATE.format(etf_ticker=ticker, etf_name=name)
    return prompt, ""


def parse_response(response: Any, etf_data: Dict[str, Any], ticker_column: str) -> Dict[str, Any]:
    """Parse the response from the model and extract the JSON data."""
    try:
        text_content = response.text
        json_start = text_content.find("{")
        json_end = text_content.rfind("}") + 1

        if json_start == -1 or json_end == -1:
            raise json.JSONDecodeError("No JSON object found", text_content, 0)

        json_text = text_content[json_start:json_end]
        response_dict = json.loads(json_text)
        etf_data.update(response_dict)
        etf_data["llm_message"] = "Success"
        return etf_data
    except Exception as e:
        ticker = etf_data.get(ticker_column, "UNKNOWN")
        logger.error(f"Issue parsing result for {ticker}: {e}")
        etf_data["llm_message"] = f"Failed to parse: {e}"
        return etf_data


def analyze_etfs(
    etf_list: List[Dict[str, Any]],
    ticker_column: str,
    name_column: str,
    client: genai.Client,
) -> List[Dict[str, Any]]:
    """Iterates through ETFs and queries Gemini for classification."""
    processed = []
    for i, etf_data in enumerate(etf_list):
        ticker = etf_data.get(ticker_column, f"ROW_{i}")
        logger.info(f"Processing {i+1}/{len(etf_list)}: {ticker}")

        prompt, error_msg = create_prompt(etf_data, ticker_column, name_column)
        if not prompt:
            etf_data["llm_message"] = error_msg
            processed.append(etf_data)
            continue

        try:
            time.sleep(1)
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
            )
            result = parse_response(response, etf_data, ticker_column)
            processed.append(result)
        except Exception as e:
            logger.error(f"Model generation failed for {ticker}: {e}")
            etf_data["llm_message"] = f"Model generation failed: {e}"
            processed.append(etf_data)
    return processed


def write_output_csv(filename: str, analysis_results: List[Dict[str, Any]]) -> None:
    """Writes the combined results to a new CSV file."""
    if not analysis_results:
        logger.warning("No analysis results to write.")
        return

    fieldnames = set()
    for row in analysis_results:
        fieldnames.update(row.keys())

    original_fields = list(analysis_results[0].keys())
    new_fields = ["category", "leverage", "income_producing", "llm_message"]

    for f in new_fields:
        if f in original_fields:
            original_fields.remove(f)

    final_fieldnames = original_fields + [f for f in new_fields if f in fieldnames]
    other_new_fields = [f for f in fieldnames if f not in final_fieldnames]
    final_fieldnames.extend(other_new_fields)

    try:
        os.makedirs(os.path.dirname(os.path.abspath(filename)), exist_ok=True)
        with open(filename, mode="w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=final_fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(analysis_results)
        logger.info(f"Successfully wrote {len(analysis_results)} rows to {filename}.")
        print(f"Successfully wrote {len(analysis_results)} rows to {filename}.")
    except Exception as e:
        logger.error(f"Error writing to CSV {filename}: {e}")


def main():
    print("Starting ETF analysis...")
    if not GOOGLE_KEY:
        print("Error: GOOGLEKEY environment variable not set.")
        return

    try:
        client = genai.Client(api_key=GOOGLE_KEY)
        logger.info(f"Google AI Client initialized for model '{MODEL_NAME}'.")
    except Exception as e:
        print(f"Error: Failed to configure Google AI: {e}")
        return

    etf_list = read_input_csv(INPUT_CSV_FILE, TICKER_COLUMN_NAME, NAME_COLUMN_NAME)
    if etf_list:
        analysis_results = analyze_etfs(etf_list, TICKER_COLUMN_NAME, NAME_COLUMN_NAME, client)
        write_output_csv(OUTPUT_CSV_FILE, analysis_results)

    print("ETF analysis complete.")


if __name__ == "__main__":
    main()
