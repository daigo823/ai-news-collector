#!/usr/bin/env python3
"""
AI News Collector
毎日 AM 2:00 JST に各ブログ/ニュースレターから記事を取得し、
Claude APIでサマリを生成してNotionに保存する。
"""

import os
import json
import hashlib
import logging
import re
import feedparser
import anthropic
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv

# .env ファイルを自動読み込み（export コマンド不要）
load_dotenv(Path(__file__).parent / ".env")

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("collector.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))

# ================== 設定 ==================

SOURCES = [
    {
        "name": "Anthropic Blog",
        # 公式RSSは廃止済み → コミュニティ管理のミラーフィードを使用
        "feed_url": "https://raw.githubusercontent.com/Olshansk/rss-feeds/refs/heads/main/feeds/feed_anthropic_news.xml",
        "tag": "Anthropic",
        # エンタープライズ・製品・事例関連に絞り込む
        "filter_keywords": [
            "enterprise", "agent", "Claude", "API", "deployment", "business",
            "partner", "case study", "customers", "tools", "model",
        ],
    },
    {
        "name": "OpenAI Blog",
        "feed_url": "https://openai.com/blog/rss.xml",
        "tag": "OpenAI",
        # エンタープライズ・製品・事例関連に絞り込む
        "filter_keywords": [
            "enterprise", "agent", "GPT", "API", "deployment", "business",
            "partner", "case study", "customers", "o1", "o3",
        ],
    },
    {
        "name": "Google DeepMind Blog",
        "feed_url": "https://deepmind.google/blog/rss.xml",
        "tag": "Google",
        # エンタープライズ・製品・事例関連に絞り込む
        "filter_keywords": [
            "enterprise", "agent", "Gemini", "API", "deployment", "business",
            "partner", "Vertex", "application", "product",
        ],
    },
    {
        "name": "a16z Newsletter",
        # 公式RSSは廃止済み → Webスクレイピングで取得
        "feed_url": None,
        "scrape_url": "https://a16z.com/news-content/",
        "tag": "a16z",
        "filter_keywords": ["AI", "LLM", "machine learning", "foundation model", "artificial intelligence", "agent"],
    },
    {
        "name": "Salesforce Engineering Blog",
        "feed_url": "https://engineering.salesforce.com/feed/",
        "tag": "Salesforce",
        # AI・エージェント関連記事に絞り込む
        "filter_keywords": ["AI", "LLM", "Agentforce", "machine learning", "agent", "artificial intelligence"],
    },
    {
        "name": "Salesforce Blog",
        "feed_url": "https://www.salesforce.com/blog/feed/",
        "tag": "Salesforce",
        # AI・エージェント関連記事に絞り込む（非AI記事が多いためフィルタ必須）
        "filter_keywords": ["AI", "LLM", "Agentforce", "machine learning", "agent", "artificial intelligence"],
    },
    {
        "name": "VentureBeat AI",
        # エンタープライズAI導入事例・CXO戦略・製品発表に特化したメディア
        "feed_url": "https://venturebeat.com/category/ai/feed/",
        "tag": "VentureBeat",
        "filter_keywords": [
            "enterprise", "agent", "agentic", "deployment", "adoption", "case study",
            "ROI", "implementation", "CTO", "CEO", "strategy", "Salesforce", "Microsoft",
            "Google", "AWS", "SAP", "ServiceNow", "workflow", "automation",
        ],
    },
]

# 既処理済み記事のIDを保存するファイル
SEEN_IDS_FILE = Path(__file__).parent / "seen_ids.json"


# ================== ユーティリティ ==================

def load_seen_ids() -> set:
    if SEEN_IDS_FILE.exists():
        with open(SEEN_IDS_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen_ids(seen: set):
    with open(SEEN_IDS_FILE, "w") as f:
        json.dump(list(seen), f, indent=2)


def article_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def is_recent(entry, hours: int = 72) -> bool:
    """published または updated が直近 hours 時間以内かチェック（デフォルト72h）"""
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            import time
            dt = datetime.fromtimestamp(time.mktime(t), tz=timezone.utc)
            return datetime.now(timezone.utc) - dt < timedelta(hours=hours)
    return True  # 日付不明な場合は処理対象とする


# ================== フィード取得 ==================

def fetch_a16z_scrape(source: dict, seen_ids: set) -> list[dict]:
    """a16z はRSSが廃止されているためWebページからリンクをスクレイピング"""
    logger.info(f"Scraping: {source['name']}")
    try:
        resp = requests.get(
            source["scrape_url"],
            headers={"User-Agent": "Mozilla/5.0 (compatible; AI-News-Collector/1.0)"},
            timeout=20,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Scrape error [{source['name']}]: {e}")
        return []

    # href="/..." の形式のリンクをすべて抽出
    links = re.findall(r'href="(https://a16z\.com/[^"]+)"', resp.text)
    # タイトル候補（aタグの中のテキスト）
    title_map = {}
    for m in re.finditer(r'href="(https://a16z\.com/[^"]+)"[^>]*>([^<]{10,120})<', resp.text):
        url, title = m.group(1), m.group(2).strip()
        if url not in title_map and title:
            title_map[url] = title

    filter_kw = [k.lower() for k in source.get("filter_keywords", [])]
    seen_urls: set = set()
    articles = []

    for url in links:
        # ページネーション・アンカーリンク等を除外
        if any(x in url for x in ["#", "?page=", "/podcast", "/video", "/category"]):
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)

        aid = article_id(url)
        if aid in seen_ids:
            continue

        title = title_map.get(url, url.split("/")[-2].replace("-", " ").title())

        # キーワードフィルタ
        if filter_kw and not any(kw in title.lower() for kw in filter_kw):
            continue

        articles.append({
            "id": aid,
            "source": source["name"],
            "tag": source["tag"],
            "title": title[:200],
            "url": url,
            "raw_summary": f"a16z の記事: {title}",
            "published": "",
        })

        if len(articles) >= 10:  # 一度に最大10件
            break

    logger.info(f"  -> {len(articles)} new article(s) from {source['name']}")
    return articles


def fetch_feed(source: dict, seen_ids: set) -> list[dict]:
    """RSSフィードを取得し、未処理の新着記事を返す"""
    # feed_url が None のソースはスクレイピングで取得
    if source.get("feed_url") is None:
        return fetch_a16z_scrape(source, seen_ids)

    logger.info(f"Fetching: {source['name']}")
    try:
        feed = feedparser.parse(source["feed_url"])
    except Exception as e:
        logger.error(f"Feed fetch error [{source['name']}]: {e}")
        return []

    articles = []
    filter_kw = [k.lower() for k in source.get("filter_keywords", [])]

    for entry in feed.entries:
        url = entry.get("link", "")
        if not url:
            continue

        aid = article_id(url)
        if aid in seen_ids:
            continue

        if not is_recent(entry):
            continue

        title = entry.get("title", "(no title)")
        summary_raw = entry.get("summary", entry.get("description", ""))

        # キーワードフィルタ
        if filter_kw:
            text = (title + " " + summary_raw).lower()
            if not any(kw in text for kw in filter_kw):
                continue

        articles.append({
            "id": aid,
            "source": source["name"],
            "tag": source["tag"],
            "title": title,
            "url": url,
            "raw_summary": summary_raw[:3000],  # Claude に渡す上限
            "published": entry.get("published", ""),
        })

    logger.info(f"  -> {len(articles)} new article(s) from {source['name']}")
    return articles


# ================== Claude サマリ ==================

def summarize(article: dict) -> str:
    """Claude API で記事のサマリを日本語で生成する"""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    prompt = f"""以下のAI関連記事を日本語でサマリしてください。

タイトル: {article['title']}
URL: {article['url']}
本文抜粋:
{article['raw_summary']}

出力形式：

【どんなもの？】
200〜300文字で、何が起きたか・何が発表されたかを説明する。

【これまでと何が違う？】
従来のアプローチや競合と比べて何が新しいのかを2〜3文で。

【ビジネスインパクトのキモはどこ？】
日本の大企業（製造業・消費財・不動産・インフラ）への影響を2〜3文で。具体的な業種・部門に言及する。

【信頼できる情報か？】
発表元・根拠・実績の有無を踏まえて1文で評価する。

【キーワード】
3つ（例：Agentforce、製造業、自動化）
"""

    try:
        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return f"サマリ生成に失敗しました: {e}"

# ================== Notion 保存 ==================

def notion_headers() -> dict:
    key = os.environ['NOTION_API_KEY']
    logger.debug(f"[DEBUG] NOTION_API_KEY in use: {key[:15]}...{key[-4:]} (len={len(key)})")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }


def create_notion_page(article: dict, summary: str):
    """Notion データベースに1記事 = 1ページを作成する"""
    database_id = os.environ["NOTION_DATABASE_ID"]

    # published を整形
    pub_str = article.get("published", "")
    published_date = None
    if pub_str:
        for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                dt = datetime.strptime(pub_str, fmt)
                published_date = dt.astimezone(JST).strftime("%Y-%m-%d")
                break
            except ValueError:
                continue

    # Notionページのプロパティ（DBスキーマに合わせて調整）
    properties = {
        "Name": {
            "title": [{"text": {"content": article["title"][:200]}}]
        },
        "Source": {
            "select": {"name": article["source"]}
        },
        "Tag": {
            "select": {"name": article["tag"]}
        },
        "URL": {
            "url": article["url"]
        },
    }
    if published_date:
        properties["Published"] = {"date": {"start": published_date}}

    # ページ本文（サマリ）
    body_blocks = []
    for line in summary.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("## "):
            body_blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {"rich_text": [{"type": "text", "text": {"content": line[3:]}}]},
            })
        elif line.startswith("- "):
            body_blocks.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": line[2:]}}]},
            })
        else:
            body_blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": line}}]},
            })

    payload = {
        "parent": {"database_id": database_id},
        "properties": properties,
        "children": body_blocks[:100],  # Notion API は一度に最大100ブロック
    }

    resp = requests.post(
        "https://api.notion.com/v1/pages",
        headers=notion_headers(),
        json=payload,
        timeout=30,
    )
    if resp.status_code not in (200, 201):
        logger.error(f"Notion API error [{resp.status_code}]: {resp.text}")
        resp.raise_for_status()

    logger.info(f"  -> Notion page created: {article['title'][:60]}")


# ================== Podcast 生成 ==================

def generate_podcast_script(articles: list) -> str:
    """収集した全記事からラジオ番組風の日本語スクリプトを生成する"""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    articles_text = "\n\n".join([
        f"【{a['source']}】{a['title']}\n{a.get('summary', a.get('raw_summary', ''))[:500]}"
        for a in articles
    ])

    prompt = f"""以下の本日のAIニュース記事をもとに、経営者・ビジネスリーダー向けのラジオ番組風日本語ナレーションスクリプトを作成してください。

{articles_text}

要件：
- 冒頭は「おはようございます。今日のエンタープライズAIニュースをお届けします。」で始める
- ビジネスインパクト・導入事例・業界動向を中心に解説する
- 経営者・CxO視点で「自社ではどう活用できるか」という視点を意識する
- 各記事を自然なナレーションでつなぐ（「続いて」「また」「次に」などの接続詞を使う）
- 専門用語はわかりやすく言い換える
- 全体で10分程度（約3000〜3500文字）を目標に、各記事を深く丁寧に解説する
- 締めは「以上、今日のエンタープライズAIニュースでした。また明日。」で終える
- マークダウン記号は使わず、読み上げに適したプレーンテキストのみで出力する
"""

    message = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def create_podcast_mp3(script: str) -> bytes:
    """OpenAI TTS APIでスクリプトをMP3に変換する"""
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    response = client.audio.speech.create(
        model="tts-1",
        voice="nova",
        input=script,
        response_format="mp3",
    )
    return response.content


def save_podcast_to_notion(script: str, date_str: str):
    """Notionに本日のPodcastスクリプトページを作成する"""
    database_id = os.environ["NOTION_DATABASE_ID"]

    # スクリプトを段落ブロックに分割
    blocks = []
    for para in script.split("\n"):
        para = para.strip()
        if not para:
            continue
        # 1ブロックあたり2000文字制限
        while len(para) > 2000:
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": para[:2000]}}]},
            })
            para = para[2000:]
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": para}}]},
        })

    payload = {
        "parent": {"database_id": database_id},
        "properties": {
            "Name": {"title": [{"text": {"content": f"📻 {date_str} AI News Podcast"}}]},
            "Source": {"select": {"name": "Podcast"}},
            "Tag": {"select": {"name": "Podcast"}},
            "URL": {"url": "https://github.com"},
            "Published": {"date": {"start": date_str}},
        },
        "children": blocks[:100],
    }

    resp = requests.post(
        "https://api.notion.com/v1/pages",
        headers=notion_headers(),
        json=payload,
        timeout=30,
    )
    if resp.status_code not in (200, 201):
        logger.error(f"Notion Podcast page error [{resp.status_code}]: {resp.text}")
        resp.raise_for_status()

    logger.info(f"  -> Notion Podcast page created: {date_str}")


# ================== メイン ==================

def main():
    logger.info("=== AI News Collector started ===")
    logger.info(f"Time (JST): {datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}")

    # 必須環境変数チェック
    for key in ("ANTHROPIC_API_KEY", "NOTION_API_KEY", "NOTION_DATABASE_ID"):
        if not os.environ.get(key):
            raise EnvironmentError(f"環境変数 {key} が設定されていません")

    seen_ids = load_seen_ids()
    new_seen: set = set()
    all_articles: list = []  # Podcast用に全記事を蓄積

    for source in SOURCES:
        articles = fetch_feed(source, seen_ids)
        for article in articles:
            try:
                logger.info(f"Summarizing: {article['title'][:60]}")
                summary = summarize(article)
                create_notion_page(article, summary)
                article["summary"] = summary
                all_articles.append(article)
                new_seen.add(article["id"])
            except Exception as e:
                logger.error(f"Failed to process [{article['title'][:40]}]: {e}")

    seen_ids |= new_seen
    save_seen_ids(seen_ids)

    logger.info(f"=== Done. {len(new_seen)} article(s) processed ===")

    # 新規記事があった日だけPodcastを生成
    if all_articles and os.environ.get("OPENAI_API_KEY"):
        logger.info("=== Generating Podcast ===")
        try:
            date_str = datetime.now(JST).strftime("%Y-%m-%d")
            script = generate_podcast_script(all_articles)
            mp3_data = create_podcast_mp3(script)
            mp3_path = Path(__file__).parent / "docs" / f"podcast_{date_str}.mp3"
            mp3_path.parent.mkdir(parents=True, exist_ok=True)
            mp3_path.write_bytes(mp3_data)
            logger.info(f"MP3 saved: {mp3_path.name} ({len(mp3_data)//1024}KB)")
            save_podcast_to_notion(script, date_str)
        except Exception as e:
            logger.error(f"Podcast generation failed: {e}")
    elif not os.environ.get("OPENAI_API_KEY"):
        logger.info("OPENAI_API_KEY not set, skipping Podcast generation")


if __name__ == "__main__":
    main()
