import hashlib
import re

from .models import Item, RawItem
from .urls import canonicalize_url


def normalize(raw: RawItem) -> Item:
    url = canonicalize_url(raw.url or raw.source_url)
    identifier = hashlib.sha256((url or f"{raw.source}:{raw.source_id}").encode()).hexdigest()[:24]
    return Item(
        identifier,
        url,
        re.sub(r"\s+", " ", raw.title).strip(),
        raw.description.strip(),
        raw.source,
        raw.source_id,
        raw.source_url,
        raw.launched_at,
        raw.observed_at,
        raw.observed_at,
        topics=raw.topics,
        metrics=raw.metrics,
        raw=raw.raw,
    )
