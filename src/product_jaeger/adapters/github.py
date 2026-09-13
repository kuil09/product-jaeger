from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..models import RawItem


class GitHub:
    def __init__(self, token: str | None = None, client: httpx.Client | None = None) -> None:
        self.token, self.client = token, client or httpx.Client(timeout=20)

    def fetch(
        self, min_stars: int, languages: list[str], since: datetime | None = None
    ) -> list[RawItem]:
        headers = {"Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        start = since or (datetime.now(timezone.utc) - timedelta(days=1))
        created_after = start.date().isoformat()
        result = []
        for language in languages:
            response = self.client.get(
                "https://api.github.com/search/repositories",
                headers=headers,
                params={
                    "q": f"created:>{created_after} stars:>={min_stars} language:{language}",
                    "sort": "stars",
                    "order": "desc",
                    "per_page": 30,
                },
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
            for repo in payload.get("items", []):
                name = str(repo.get("full_name") or "")
                created = repo.get("created_at")
                result.append(
                    RawItem(
                        "github_search",
                        name,
                        str(repo.get("html_url") or ""),
                        name,
                        str(repo.get("description") or ""),
                        str(repo.get("html_url") or ""),
                        datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                        if created
                        else None,
                        list(map(str, repo.get("topics", []))),
                        {
                            "stars": float(repo.get("stargazers_count") or 0),
                            "forks": float(repo.get("forks_count") or 0),
                        },
                        repo,
                    )
                )
        return result
