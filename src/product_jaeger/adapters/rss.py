import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx

from ..models import RawItem


def text(node: ET.Element | None) -> str:
    return " ".join("".join(node.itertext()).split()) if node is not None else ""


class RSS:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(timeout=20)

    def fetch(self, feeds: list[str]) -> list[RawItem]:
        result = []
        for feed in feeds:
            response = self.client.get(feed, headers={"User-Agent": "ProductJaeger/0.1"})
            response.raise_for_status()
            root = ET.fromstring(response.content)
            entries = root.findall(".//item") + root.findall(
                ".//{http://www.w3.org/2005/Atom}entry"
            )
            for entry in entries:
                atom_link = entry.find("{http://www.w3.org/2005/Atom}link")
                link = entry.find("link")
                url = (
                    (link.text or "").strip()
                    if link is not None and link.text
                    else str(atom_link.attrib.get("href") or "")
                    if atom_link is not None
                    else ""
                )
                identifier = (
                    text(entry.find("guid"))
                    or text(entry.find("{http://www.w3.org/2005/Atom}id"))
                    or url
                )
                result.append(
                    RawItem(
                        "rss",
                        f"{feed}:{identifier}",
                        url or feed,
                        text(entry.find("title"))
                        or text(entry.find("{http://www.w3.org/2005/Atom}title")),
                        text(entry.find("description"))
                        or text(entry.find("{http://www.w3.org/2005/Atom}summary")),
                        url,
                        raw={"feed": feed, "id": identifier},
                        observed_at=datetime.now(timezone.utc),
                    )
                )
        return result
