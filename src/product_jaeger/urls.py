from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING = {"ref", "from", "source", "campaign", "mc_cid", "mc_eid"}


def canonicalize_url(url: str) -> str:
    if not url:
        return ""
    value = url.strip()
    if "://" not in value:
        value = "https://" + value
    parts = urlsplit(value)
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    if host == "github.com":
        bits = [bit for bit in path.split("/") if bit]
        path = "/" + "/".join(bits[:2]) if len(bits) >= 2 else path
        query = []
    else:
        query = [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith("utm_") and k.lower() not in TRACKING
        ]
    return urlunsplit(((parts.scheme or "https").lower(), host, path, urlencode(query), ""))
