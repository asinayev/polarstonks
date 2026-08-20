"""Google Custom Search Engine integration with pagination support."""

import logging
import requests
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


def search_custom_engine(
    google_key: str,
    cse_id: str,
    query: str,
    n_results: int = 90,
    **kwargs: Any,
) -> List[Dict[str, Any]]:
    """Fetches custom search results using Google Custom Search JSON API.

    Args:
        google_key: Google Cloud API key with Custom Search API enabled.
        cse_id: Custom Search Engine ID (CX).
        query: Search query string.
        n_results: Maximum number of search results to fetch (default: 90).
        **kwargs: Additional query parameters (e.g., sort='date', orTerms='...').

    Returns:
        List of item dictionaries returned by the Google Custom Search API.
    """
    all_results: List[Dict[str, Any]] = []
    start_index = 1
    endpoint = "https://customsearch.googleapis.com/customsearch/v1"

    while start_index <= n_results:
        params: Dict[str, Any] = {
            "key": google_key,
            "cx": cse_id,
            "q": query,
            "start": start_index,
            "num": min(10, n_results - start_index + 1),
        }
        params.update(kwargs)

        try:
            resp = requests.get(endpoint, params=params, timeout=15)
            if resp.status_code != 200:
                logger.error(f"Google Search API error {resp.status_code}: {resp.text}")
                break

            data = resp.json()
            items = data.get("items", [])
            if not items:
                break

            all_results.extend(items)
            start_index += len(items)

            # If fewer items than requested were returned, we have reached the end
            if len(items) < min(10, n_results - start_index + 1):
                break

        except Exception as e:
            logger.error(f"Exception during Google search query '{query}': {e}")
            break

    return all_results


def all_search_pages(service: Any, cse_id: str, n_results: int = 90, **kwargs: Any) -> List[Dict[str, Any]]:
    """Legacy interface using google-api-python-client discovery service object."""
    all_results: List[Dict[str, Any]] = []
    search_i = 1
    while search_i < n_results:
        try:
            results = service.list(cx=cse_id, start=search_i, **kwargs).execute()
            if "items" in results:
                all_results += results["items"]
                search_i += 10
            else:
                break
        except Exception as e:
            logger.error(f"Error querying custom search service: {e}")
            break
    return all_results
