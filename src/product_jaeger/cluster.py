import re
from difflib import SequenceMatcher

from .models import Item
from .urls import canonicalize_url


def _similar(left: str, right: str) -> float:
    a = set(re.findall(r"[a-z0-9가-힣]+", left.lower()))
    b = set(re.findall(r"[a-z0-9가-힣]+", right.lower()))
    if not a or not b:
        return 0.0
    return max(
        len(a & b) / len(a | b),
        SequenceMatcher(None, " ".join(sorted(a)), " ".join(sorted(b))).ratio(),
    )


def cluster(items: list[Item], threshold: float = 0.86) -> list[Item]:
    representatives: list[Item] = []
    for item in items:
        item.canonical_url = canonicalize_url(item.canonical_url)
        match = next(
            (
                representative
                for representative in representatives
                if (item.canonical_url and item.canonical_url == representative.canonical_url)
                or (
                    item.canonical_url
                    and representative.canonical_url
                    and item.canonical_url.split("/", 3)[:3]
                    == representative.canonical_url.split("/", 3)[:3]
                    and _similar(item.title, representative.title) >= threshold
                )
            ),
            None,
        )
        if match:
            item.cluster_id = match.cluster_id
        else:
            item.cluster_id = item.id
            representatives.append(item)
    groups: dict[str, set[str]] = {}
    for item in items:
        groups.setdefault(item.cluster_id or item.id, set()).add(item.source)
    for item in items:
        item.sources = sorted(groups[item.cluster_id or item.id])
    return items
