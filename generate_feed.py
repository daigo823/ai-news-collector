#!/usr/bin/env python3
"""
generate_feed.py
docs/ フォルダのMP3ファイルをスキャンして、
Apple Podcast対応のRSSフィード（docs/feed.xml）を生成・更新する。
"""

import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from email.utils import formatdate
import time

BASE_URL = "https://daigo823.github.io/ai-news-collector"
DOCS_DIR = Path(__file__).parent / "docs"
FEED_PATH = DOCS_DIR / "feed.xml"

CHANNEL_TITLE = "AI News Daily"
CHANNEL_DESCRIPTION = "毎日のAIニュースをお届けするPodcast。Anthropic・OpenAI・Google・a16z・Salesforceの最新情報を日本語でサマリします。"
CHANNEL_AUTHOR = "Daigo Mizoguchi"
CHANNEL_LANGUAGE = "ja"


def mp3_to_pubdate(filename: str) -> str:
    """podcast_YYYY-MM-DD.mp3 → RFC 2822形式の日付文字列"""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", filename)
    if m:
        dt = datetime.strptime(m.group(1), "%Y-%m-%d").replace(
            tzinfo=timezone(timedelta(hours=9))
        )
        return formatdate(dt.timestamp(), usegmt=True)
    return formatdate(usegmt=True)


def build_feed(episodes: list[dict]) -> str:
    """エピソードのリストからRSS XML文字列を生成する"""
    items_xml = ""
    for ep in episodes:
        items_xml += f"""
    <item>
      <title>{ep["title"]}</title>
      <pubDate>{ep["pub_date"]}</pubDate>
      <enclosure url="{ep["url"]}" type="audio/mpeg" length="{ep["length"]}"/>
      <guid isPermaLink="true">{ep["url"]}</guid>
      <itunes:duration>{ep.get("duration", "")}</itunes:duration>
    </item>"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>{CHANNEL_TITLE}</title>
    <link>{BASE_URL}/</link>
    <description>{CHANNEL_DESCRIPTION}</description>
    <language>{CHANNEL_LANGUAGE}</language>
    <lastBuildDate>{formatdate(usegmt=True)}</lastBuildDate>
    <itunes:author>{CHANNEL_AUTHOR}</itunes:author>
    <itunes:summary>{CHANNEL_DESCRIPTION}</itunes:summary>
    <itunes:category text="Technology"/>
    <itunes:explicit>false</itunes:explicit>
{items_xml}
  </channel>
</rss>
"""


def main():
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    # docs/ 内のMP3を日付降順で列挙
    mp3_files = sorted(DOCS_DIR.glob("podcast_*.mp3"), reverse=True)
    print(f"Found {len(mp3_files)} MP3 file(s)")

    episodes = []
    for mp3 in mp3_files:
        filename = mp3.name
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", filename)
        date_str = date_match.group(1) if date_match else "Unknown"

        episodes.append({
            "title": f"{date_str} AI News",
            "pub_date": mp3_to_pubdate(filename),
            "url": f"{BASE_URL}/{filename}",
            "length": mp3.stat().st_size,
        })

    xml = build_feed(episodes)
    FEED_PATH.write_text(xml, encoding="utf-8")
    print(f"feed.xml updated: {len(episodes)} episode(s) → {FEED_PATH}")


if __name__ == "__main__":
    main()
