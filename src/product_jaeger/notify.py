import os
from html import escape

import httpx

from .models import Item


def format_message(items: list[Item]) -> str:
    lines = ["<b>Product Jaeger Early Radar</b>", ""]
    for n, item in enumerate(items, 1):
        summary = item.llm_summary or item.description[:240] or "설명 없음"
        lines += [
            f'<b>{n}. <a href="{escape(item.canonical_url)}">{escape(item.title)}</a></b>',
            escape(summary),
            f"출처: {escape(' · '.join(item.sources or [item.source]))} · score {item.final_score:.3f}",
            "",
        ]
    return "\n".join(lines).strip()


def send(items: list[Item]) -> None:
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        raise RuntimeError("Telegram credentials are missing")
    message = format_message(items)
    for start in range(0, max(len(message), 1), 3900):
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat,
                "text": message[start : start + 3900],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        response.raise_for_status()
