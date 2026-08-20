"""Web article extraction, LLM evaluation with Google GenAI, and Polygon data enrichment."""

import re
import json
import html
import logging
import time
from typing import List, Dict, Any, Optional
import requests
from google.genai import types

logger = logging.getLogger(__name__)


def fetch_article(url: str) -> Optional[str]:
    """Fetches web page content and extracts main text using Standard Library tools only.

    No BeautifulSoup or Trafilatura required.
    """
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
        }
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        raw_html = response.text

        # 1. Remove JavaScript and CSS
        clean_html = re.sub(
            r"<(script|style|head|title|noscript)[^>]*>.*?</\1>",
            " ",
            raw_html,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # 2. Remove HTML Comments
        clean_html = re.sub(r"<!--.*?-->", " ", clean_html, flags=re.DOTALL)

        # 3. Strip all remaining HTML tags
        text_only = re.sub(r"<[^>]+>", "\n", clean_html)

        # 4. Unescape HTML entities (e.g., "&amp;" -> "&", "&nbsp;" -> " ")
        text_only = html.unescape(text_only)

        # 5. Remove last quarter of the text as it usually contains footers / links to other articles
        cutoff_index = int(len(text_only) * 0.75)
        text_only = text_only[:cutoff_index]
        return text_only
    except Exception as e:
        logger.debug(f"Could not extract article content from {url}: {e}")
        return None


def create_prompt(prompt_template: str, result: Dict[str, Any]) -> str:
    """Creates an AI evaluation prompt from the template and search result data."""
    full_article = None
    if "link" in result:
        full_article = fetch_article(result["link"])

    search_data = (
        f"\n\nSearch Result Details:\n"
        f"Title: {result.get('title', 'Unknown')}\n"
        f"Link: {result.get('link', 'Unknown')}\n"
    )

    if full_article and len(full_article.strip()) > 100:
        search_data += f"Full Article: {full_article}\n"
    else:
        # Fallback to snippet if full article fetch failed or was too short
        search_data += f"Snippet: {result.get('snippet', 'No snippet available')}\n"

    if "pagemap" in result and isinstance(result["pagemap"], dict):
        metatags = result["pagemap"].get("metatags", [])
        if metatags and isinstance(metatags, list) and len(metatags) > 0:
            meta = metatags[0]
            meta_hints = [
                f"{k}: {meta[k].strip()}"
                for k in ["og:symbol", "ticker", "stock_symbol", "keywords", "og:description"]
                if k in meta and isinstance(meta[k], str) and meta[k].strip()
            ]
            if meta_hints:
                search_data += "Metadata Hints: " + "; ".join(meta_hints) + "\n"

    return prompt_template + search_data


def parse_response(response: Any, result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parses JSON response from Gemini model and merges it into the result dictionary."""
    try:
        raw_text = response.text
        if not raw_text:
            return None

        clean_text = re.sub(r"```json\s*|\s*```", "", raw_text).strip()
        ai_data = json.loads(clean_text)
        result.update(ai_data)
        return result

    except json.JSONDecodeError:
        logger.error(f"Failed to parse JSON from AI response: {getattr(response, 'text', '')[:100]}...")
        return None
    except Exception as e:
        logger.error(f"Error processing AI response: {e}")
        return None


def read_results(
    all_results: List[Dict[str, Any]],
    prompt_template: str,
    client: Any,
    model_id: str,
    config: Any,
) -> List[Dict[str, Any]]:
    """Evaluates search results using Google GenAI with rate-limit retries."""
    valid_summaries: List[Dict[str, Any]] = []

    for result in all_results:
        result["message"] = ""
        prompt = create_prompt(prompt_template, result)

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model=model_id,
                    contents=prompt,
                    config=config,
                )

                summary = parse_response(response, result)
                if summary:
                    valid_summaries.append(summary)

                time.sleep(1)
                break

            except Exception as e:
                error_msg = str(e)
                if "429" in error_msg:
                    wait_time = (2 ** attempt) + 2
                    logger.warning(f"Rate limit (429) hit. Waiting {wait_time}s before retry {attempt+1}/{max_retries}...")
                    time.sleep(wait_time)
                    if attempt == max_retries - 1:
                        logger.error(f"Max retries reached for {result.get('link')}")
                else:
                    logger.error(f"Model generation error: {e}")
                    break

    return valid_summaries


def enrich_result(
    result: Dict[str, Any],
    poly_client: Any,
    parameters: Dict[str, Any],
) -> Dict[str, Any]:
    """Enriches an AI-verified result with financial metrics from Polygon API."""
    result["match"] = False
    ticker = result.get("ticker", "").strip().upper()

    if not ticker or ticker == "UNKNOWN":
        result["message"] += "No ticker"
        if "companyName" in result:
            result["ticker"] = "_" + str(result["companyName"])
        return result

    first_match = None
    market_cap = None
    snap = None

    try:
        company_name = result.get("companyName", "")
        names_to_try = [
            company_name,
            "".join(ch for ch in company_name if ch.isalnum() or ch == " "),
            company_name.rsplit(" ", 1)[0] if " " in company_name else company_name,
            company_name.split(" ")[0] if " " in company_name else company_name,
        ]
        for n in names_to_try:
            try:
                matches = poly_client.list_tickers(search=n, active=True, type="CS")
                first_match = next(matches)
                if first_match.ticker == ticker:
                    break
            except Exception:
                continue

        if not first_match or first_match.ticker != ticker:
            result["message"] += "No good match in polygon for ticker"

        details = poly_client.get_ticker_details(ticker=ticker)
        market_cap = getattr(details, "market_cap", None)
        snap = poly_client.get_snapshot_ticker(ticker=ticker, market_type="stocks")

    except Exception as e:
        logger.debug(f"Issue fetching polygon details for {ticker}: {e}")
        result["message"] += " Issue getting polygon data"

    try:
        market_cap_ok = False
        if market_cap is not None:
            market_cap_ok = market_cap < parameters.get("max_market_cap", float("inf"))

        volume = None
        current = None
        liquidity_ok = False
        overnight_in_range = False

        if snap:
            if getattr(snap, "prev_day", None):
                volume = snap.prev_day.volume
                if snap.prev_day.close is not None:
                    if getattr(snap, "todays_change", None) is not None:
                        current = snap.prev_day.close + snap.todays_change
                    if snap.prev_day.volume is not None:
                        liquidity_ok = (
                            snap.prev_day.close > parameters.get("min_close", 0)
                            and snap.prev_day.volume > parameters.get("min_volume", 0)
                        )

            if getattr(snap, "todays_change_percent", None) is not None:
                overnight_in_range = (
                    parameters.get("min_overnight_pchange", -100)
                    < snap.todays_change_percent
                    < parameters.get("max_overnight_pchange", 100)
                )

        result.update({
            "market_cap_ok": market_cap_ok,
            "volume": volume,
            "current": current,
            "liquidity_ok": liquidity_ok,
            "overnight_in_range": overnight_in_range,
        })
        result["message"] += " " + generate_message(result)
        result["match"] = (
            bool(first_match)
            and first_match.ticker == ticker
            and result["liquidity_ok"]
            and result["market_cap_ok"]
            and result["overnight_in_range"]
        )
        if "No good match in polygon for ticker" in result["message"]:
            result["message"] += " | DOUBLE CHECK THIS TICKER"
            result["match"] = True

    except Exception as e:
        logger.error(f"Error evaluating polygon metrics for {ticker}: {e}")
        result["message"] += " Error evaluating metrics"

    return result


def generate_message(result: Dict[str, Any]) -> str:
    """Generates failure description for filtering criteria."""
    issues = ["liquidity_ok", "market_cap_ok", "overnight_in_range"]
    messages = [f"failed {issue}" for issue in issues if not result.get(issue, False)]
    return " ".join(messages)
