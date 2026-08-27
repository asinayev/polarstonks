"""Corporate announcements scanner (share buybacks & raised guidance) using Google Search and Gemini LLM."""

import os
import sys
import logging
from typing import List, Dict, Any, Tuple
from google import genai
from google.genai import types

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.config import GOOGLE_KEY, POLYGON_KEY, SEARCH_ID, OUT_DIR
from src.llm_parsing.search_pipeline import fetch_search_results
from src.llm_parsing.article_evaluator import read_results, enrich_result
from polygon import RESTClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def run_announcement_search(
    scan_name: str,
    parameters: Dict[str, Any],
    client: genai.Client,
    model_id: str,
    config: types.GenerateContentConfig,
    polygon_key: str,
    search_id: str,
    google_key: str,
    f: Any = None,
) -> Tuple[int, List[Dict[str, Any]]]:
    """Runs the Google CSE search, AI processing, and Polygon enrichment in batches."""
    logger.info(f"--- Starting Google Search for: {scan_name} ---")

    raw_results = fetch_search_results(
        search_service=None,
        search_id=search_id,
        query=parameters["search_query"],
        n_results=90,
        google_key=google_key,
    )

    if not raw_results:
        logger.warning(f"No Google search results for '{scan_name}'.")
        return 0, []

    logger.info(f"Found {len(raw_results)} Google search results to process for '{scan_name}'.")

    # Log top results
    for idx, r in enumerate(raw_results[:5]):
        pagemap = r.get("pagemap", {})
        date_str = None
        if pagemap and "metatags" in pagemap and pagemap["metatags"]:
            first_meta = pagemap["metatags"][0]
            date_str = (
                first_meta.get("date")
                or first_meta.get("article:published_time")
                or first_meta.get("pubdate")
                or first_meta.get("og:pubdate")
                or first_meta.get("datepublished")
            )
        logger.info(f"  {idx+1}. Date: {date_str} | Title: {r.get('title')} | Link: {r.get('link')}")

    all_matches = []
    batch_size = 10
    poly_client = RESTClient(api_key=polygon_key) if polygon_key else None

    for i in range(0, len(raw_results), batch_size):
        batch = raw_results[i : i + batch_size]
        logger.info(f"Processing batch {i//batch_size + 1} ({len(batch)} results) for {scan_name}...")

        processed = read_results(batch, parameters["prompt_template"], client, model_id, config)

        for res in processed:
            is_new = str(res.get("newProgram", "")).strip().lower()
            if is_new == "yes":
                if poly_client:
                    try:
                        enriched = enrich_result(res, poly_client, parameters)
                    except Exception as e:
                        logger.error(f"Error enriching {res.get('ticker')}: {e}")
                        enriched = res
                else:
                    enriched = res

                all_matches.append(enriched)
                ticker = enriched.get("ticker", "UNKNOWN")
                company = enriched.get("companyName", "Unknown Company")
                dt = enriched.get("timePublished", "Unknown Date")
                quote = enriched.get("quote", "")
                link = enriched.get("link", "")
                logger.info(f"MATCH FOUND: [{ticker}] {company} ({dt}) - Quote: '{quote}' | Link: {link}")

    if f and all_matches:
        write_matches_to_file(all_matches, f)

    return len(raw_results), all_matches


def write_matches_to_file(matches: List[Dict[str, Any]], f: Any) -> None:
    """Writes deduplicated announcement matches to the summary file."""
    unique_matches = {}
    for m in matches:
        key = (m.get("ticker", "UNKNOWN"), m.get("companyName", "Unknown Company"))
        if key not in unique_matches:
            unique_matches[key] = {
                "info": m,
                "links": set(),
                "quotes": [],
            }
        if m.get("link"):
            unique_matches[key]["links"].add(m.get("link"))

        q = m.get("quote", "").strip()
        if q:
            unique_matches[key]["quotes"].append(q)

    for key, data in unique_matches.items():
        m = data["info"]
        ticker = m.get("ticker", "UNKNOWN")
        company = m.get("companyName", "Unknown Company")
        date_val = m.get("timePublished", "Unknown Date")

        quotes = data["quotes"]
        quote = max(quotes, key=len) if quotes else ""

        f.write(f"[{ticker}] {company} ({date_val})\n")
        if quote:
            f.write(f'  "...{quote}..."\n')

        f.write("  Sources:\n")
        for link in sorted(list(data["links"])):
            f.write(f"  - {link}\n")
        f.write("\n")


def main():
    """Main entry point for announcements scanning."""
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

    prompt_template_base = """
Determine if the following search result includes a NEW announcement about a {}  Answer 'yes' or 'no'.

For your response, also extract (or infer) the following:
- Company Full Name
- Stock Ticker Symbol
- Publication Date and Time
- A short quote from the search result that supports your 'yes'/'no' determination.

1. Locate the title. If the title doesn't refer to the company at all, RESPOND NO.  
2. Locate the publication time. Reformat to YYYY-MM-DD HH:MM using military time.
3. Read the rest of the announcement. Pay special attention to the link and title.
4. If the explicit ticker symbol is omitted from the snippet text, use your knowledge of public company listings to infer the official primary stock exchange ticker symbol for the company name (e.g. 'SSR Mining' -> 'SSRM', 'TotalEnergies' -> 'TTE', 'Pershing Square' -> 'PSH'). Only output 'UNKNOWN' if the company is strictly private or unlisted.

Respond in JSON format as shown in this example:
{{"newProgram":"No","companyName":"Microsoft Corporation","ticker":"MSFT","timePublished":"2024-08-12 15:30","quote":"In June, the board announced..."}}

Search result:
"""

    # --- BUYBACKS ---
    buyback_desc = (
        "an announcement of a share buyback or share repurchase program. "
        "If the announcement does not say that stock will be repurchased or bought back IN THE FUTURE "
        "(e.g., is simply an update about past repurchases), respond no. Also, if the buyback is funded by "
        "or part of a debt/note issuance, or is specifically meant to offset some other corporate financial action "
        "(such as dilution from option exercises or stock-based compensation), respond no."
    )
    buyback_params = {
        "search_query": "buyback|repurchase share",
        "prompt_template": prompt_template_base.format(buyback_desc),
        "min_close": 5,
        "min_volume": 20000,
        "max_market_cap": 10000000000,
        "min_overnight_pchange": -2.2,
        "max_overnight_pchange": 9,
    }

    # --- GUIDANCE ---
    guidance_desc = (
        "improved or raised guidance, outlook, or forecast in the newProgram field. The announcement should say that "
        "guidance, outlook, or forecast for an important metric is now higher (better) than previously expected. "
        "If it's just a routine update or confirming prior guidance, respond no."
    )
    guidance_params = {
        "search_query": "improve|improves|improved|improving|raise|raises|raised|raising|increase|increases|increased|increasing guidance|outlook|forecast",
        "prompt_template": prompt_template_base.format(guidance_desc),
        "min_close": 5,
        "min_volume": 20000,
        "max_market_cap": 100000000000,
        "min_overnight_pchange": -0.2,
        "max_overnight_pchange": 20,
    }

    output_dir = OUT_DIR
    os.makedirs(output_dir, exist_ok=True)

    summary_path = os.path.join(output_dir, "steves_news.txt")
    announcements_path = os.path.join(output_dir, "corporate_announcements.txt")

    import io
    report_buffer = io.StringIO()
    report_buffer.write("CORPORATE ANNOUNCEMENTS & NEWS SCANNER - SUMMARY\n")
    report_buffer.write("================================================\n\n")

    # --- Buybacks Section ---
    report_buffer.write("=== BUYBACKS & REPURCHASES ===\n")
    total_bb, matches_bb = run_announcement_search(
        "buyback_scan",
        buyback_params,
        client,
        model_id,
        config,
        POLYGON_KEY,
        SEARCH_ID,
        GOOGLE_KEY,
        f=report_buffer,
    )
    if not matches_bb:
        report_buffer.write("No matching opportunities found.\n")

    report_buffer.write("\n")

    # --- Guidance Section ---
    report_buffer.write("=== GUIDANCE & OUTLOOK ===\n")
    total_guide, matches_guide = run_announcement_search(
        "guidance_scan",
        guidance_params,
        client,
        model_id,
        config,
        POLYGON_KEY,
        SEARCH_ID,
        GOOGLE_KEY,
        f=report_buffer,
    )
    if not matches_guide:
        report_buffer.write("No matching opportunities found.\n")

    final_report = report_buffer.getvalue()

    for path in [summary_path, announcements_path]:
        try:
            with open(path, "w") as f:
                f.write(final_report)
            logger.info(f"Summary saved to {path}")
        except PermissionError:
            fallback = f"/tmp/{os.path.basename(path)}"
            logger.warning(f"Permission denied writing to {path} (likely owned by root from Docker). Writing to fallback {fallback} instead.")
            try:
                with open(fallback, "w") as f:
                    f.write(final_report)
                logger.info(f"Summary saved to fallback {fallback}")
            except Exception as e:
                logger.error(f"Failed to write fallback to {fallback}: {e}")
        except Exception as e:
            logger.error(f"Error writing to {path}: {e}")

    print(final_report)


if __name__ == "__main__":
    main()
