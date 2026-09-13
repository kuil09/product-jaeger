from datetime import datetime, timezone
from typing import Any

import httpx

from ..models import RawItem


class HN:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(timeout=20)

    def fetch(self, since: int) -> list[RawItem]:
        response = self.client.get(
            "https://hn.algolia.com/api/v1/search_by_date",
            params={
                "tags": "show_hn",
                "hitsPerPage": 50,
                "numericFilters": f"created_at_i>{since}",
            },
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        result = []
        for hit in payload.get("hits", []):
            identifier = str(hit.get("objectID", ""))
            created = hit.get("created_at_i")
            result.append(
                RawItem(
                    "hn_show",
                    identifier,
                    f"https://news.ycombinator.com/item?id={identifier}",
                    str(hit.get("title") or ""),
                    str(hit.get("story_text") or ""),
                    str(hit.get("url") or ""),
                    datetime.fromtimestamp(int(created), timezone.utc) if created else None,
                    metrics={
                        "points": float(hit.get("points") or 0),
                        "comments": float(hit.get("num_comments") or 0),
                    },
                    raw=hit,
                )
            )
        return result
