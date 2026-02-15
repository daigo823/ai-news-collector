# AI News Collector

毎日 AM 2:00 JST に AI 関連ブログを巡回し、Claude API でサマリを生成して Notion に保存するツール。

## 対象ソース

| ソース | フィードURL |
|--------|------------|
| Anthropic Blog | https://www.anthropic.com/rss.xml |
| OpenAI Blog | https://openai.com/blog/rss.xml |
| Google DeepMind Blog | https://deepmind.google/blog/rss.xml |
| a16z Newsletter | https://a16z.com/feed/ ※AIタグ絞り込み |

## セットアップ

### 1. 依存パッケージのインストール

```bash
cd ai-news-collector
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 環境変数の設定

```bash
cp .env.example .env
# .env を編集して各APIキーを設定
```

#### Notion の準備

1. https://www.notion.so/my-integrations でインテグレーションを作成
2. 以下のスキーマを持つデータベースを Notion に作成:

| プロパティ名 | 種類 |
|------------|------|
| Name | タイトル |
| Source | セレクト |
| Tag | セレクト |
| URL | URL |
| Published | 日付 |

3. データベースページを開き、右上「…」→「コネクト」でインテグレーションを追加
4. データベースIDをURLから取得: `notion.so/{workspace}/{DATABASE_ID}?v=...`

### 3. 動作確認

```bash
source .env  # または: export $(cat .env | xargs)
python3 collector.py
```

### 4. cron の設定（毎日 AM 2:00 JST）

```bash
bash setup_cron.sh
```

手動で追加する場合（crontab -e）:

```
0 17 * * * cd /path/to/ai-news-collector && /usr/bin/python3 collector.py >> cron.log 2>&1
```

※ JST 02:00 = UTC 17:00

## ログ

- `collector.log` : スクリプト実行ログ
- `cron.log` : cron 実行ログ
- `seen_ids.json` : 処理済み記事IDのキャッシュ（重複防止）

## Notion ページ出力例

```
タイトル: Claude 3.7 Sonnet のリリース

## 概要
Anthropic が Claude 3.7 Sonnet をリリース。...

## 主なポイント
- 推論能力が大幅に向上
- ...

## 重要度
High - 最新モデルのリリースであり業界への影響が大きい
```
