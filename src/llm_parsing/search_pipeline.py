"""Search result processing, date sorting, and CSV pipeline."""

import os
import logging
from typing import List, Dict, Any, Optional
import pandas as pd
from polygon import RESTClient

from src.llm_parsing.google_search import search_custom_engine, all_search_pages
from src.llm_parsing.article_evaluator import (
    read_results,
    enrich_result,
)

logger = logging.getLogger(__name__)


def extract_date(item: Dict[str, Any]) -> Optional[str]:
    """Extracts date from a search result item's pagemap metadata."""
    pagemap = item.get("pagemap", {})
    if not pagemap:
        return None
    metatags = pagemap.get("metatags", [])
    if not metatags:
        return None
    first_meta = metatags[0]

    date_keys = [
        "date",
        "article:published_time",
        "pubdate",
        "og:pubdate",
        "datepublished",
        "published_time",
        "sailthru.date",
    ]
    for key in date_keys:
        if key in first_meta:
            val = first_meta[key]
            if isinstance(val, str) and val.strip():
                return val.strip()

    for key, val in first_meta.items():
        k_lower = key.lower()
        if "date" in k_lower or "published" in k_lower or "pubdate" in k_lower:
            if isinstance(val, str) and val.strip():
                return val.strip()

    return None


def sort_results_by_date(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sorts search results by publication date in descending order (newest first)."""
    def sort_key(item):
        date_str = extract_date(item)
        if not date_str:
            return pd.Timestamp.min.tz_localize("UTC")
        try:
            parsed_date = pd.to_datetime(date_str, utc=True)
            if pd.isna(parsed_date):
                return pd.Timestamp.min.tz_localize("UTC")
            return parsed_date
        except Exception as e:
            logger.debug(f"Error parsing date string '{date_str}': {e}")
            return pd.Timestamp.min.tz_localize("UTC")

    return sorted(results, key=sort_key, reverse=True)


def fetch_search_results(
    search_service: Any,
    search_id: str,
    query: str,
    n_results: int = 90,
    google_key: Optional[str] = None,
    **kwargs: Any,
) -> List[Dict[str, Any]]:
    """Fetches search results and sorts them by date descending."""
    try:
        if search_service is not None:
            all_results = all_search_pages(
                service=search_service,
                cse_id=search_id,
                n_results=n_results,
                q=query,
                sort="date",
                num=10,
                **kwargs,
            )
        elif google_key:
            all_results = search_custom_engine(
                google_key=google_key,
                cse_id=search_id,
                query=query,
                n_results=n_results,
                sort="date",
                **kwargs,
            )
        else:
            logger.error("Neither search_service nor google_key provided.")
            return []

        logger.info(f"{len(all_results)} total results found for query: {query}")
        sorted_results = sort_results_by_date(all_results)
        return sorted_results
    except Exception as e:
        logger.error(f"Error fetching search results: {e}")
        return []


def process_results_with_ai(
    all_results: List[Dict[str, Any]],
    prompt_template: str,
    client: Any,
    model_id: str,
    config: Any,
) -> List[Dict[str, Any]]:
    """Processes search results using the Gemini AI model."""
    try:
        valid_results = read_results(
            all_results,
            prompt_template=prompt_template,
            client=client,
            model_id=model_id,
            config=config,
        )
        logger.info(f"{len(valid_results)} valid results after AI processing.")
        return valid_results
    except Exception as e:
        logger.error(f"Error processing results with AI: {e}")
        return []


def enrich_with_financial_data(
    valid_results: List[Dict[str, Any]],
    polygon_key: str,
    parameters: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Enriches results with stock market data from Polygon API."""
    enriched_results = []
    poly_client = RESTClient(api_key=polygon_key)
    for r in valid_results:
        try:
            enriched = enrich_result(r, poly_client, parameters)
            enriched_results.append(enriched)
        except Exception as e:
            logger.error(f"Error enriching result for {r.get('ticker')}: {e}")
    logger.info(f"{len(enriched_results)} results enriched with Polygon data.")
    return enriched_results


def format_and_save_results(
    enriched_results: List[Dict[str, Any]],
    query: str,
    write_to_dir: str,
) -> None:
    """Formats the results and saves them to a CSV file."""
    try:
        all_data = []
        for r in enriched_results:
            try:
                if isinstance(r.get("newProgram"), str):
                    r["newProgram"] = r["newProgram"].lower()
                if isinstance(r.get("quote"), str):
                    r["quote"] = r["quote"].lower()
            except Exception as e:
                logger.error(f"Error cleaning result fields: {e}")
            all_data.append(r)

        if not all_data:
            logger.info("No data to save.")
            return

        columns = [
            "timePublished", "ticker", "match", "message", "newProgram", "quote",
            "title", "link", "companyName", "current", "volume", "market_cap_ok",
            "liquidity_ok", "overnight_in_range"
        ]
        df = pd.DataFrame(all_data).reindex(columns=columns)

        if not df.empty and "newProgram" in df.columns:
            df.sort_values(["newProgram", "timePublished"], ascending=False, inplace=True)

        os.makedirs(write_to_dir, exist_ok=True)
        out_filename = query.replace("|", "_").replace('"', "").replace(" ", "_") + ".csv"
        out_path = os.path.join(write_to_dir, out_filename)
        df.to_csv(out_path, index=False)
        logger.info(f"Saved results to {out_path}")

    except Exception as e:
        logger.error(f"Error formatting and saving results: {e}")


def read_search(
    google_key: str,
    polygon_key: str,
    search_id: str,
    client: Any,
    model_id: str,
    config: Any,
    write_to_dir: str,
    parameters: Dict[str, Any],
    n_results: int = 90,
    **kwargs: Any,
) -> None:
    """Performs search, runs AI classification, enriches with Polygon, and saves CSV."""
    search_results = fetch_search_results(
        search_service=None,
        search_id=search_id,
        query=parameters["search_query"],
        n_results=n_results,
        google_key=google_key,
        **kwargs,
    )

    processed_results = process_results_with_ai(
        search_results,
        prompt_template=parameters["prompt_template"],
        client=client,
        model_id=model_id,
        config=config,
    )

    enriched_results = enrich_with_financial_data(processed_results, polygon_key, parameters)
    format_and_save_results(enriched_results, parameters["search_query"], write_to_dir)
