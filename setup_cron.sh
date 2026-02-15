#!/bin/bash
# cron ジョブのセットアップスクリプト
# 実行: bash setup_cron.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="$(which python3)"
LOG_FILE="$SCRIPT_DIR/cron.log"

# 既存の同一エントリを削除してから追加
CRON_CMD="0 17 * * * cd \"$SCRIPT_DIR\" && $PYTHON_BIN \"$SCRIPT_DIR/collector.py\" >> \"$LOG_FILE\" 2>&1"
# ※ JST 02:00 = UTC 17:00 (前日)

(crontab -l 2>/dev/null | grep -v "collector.py"; echo "$CRON_CMD") | crontab -

echo "cron ジョブを登録しました:"
echo "  $CRON_CMD"
echo ""
echo "確認: crontab -l"
