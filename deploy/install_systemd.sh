#!/usr/bin/env bash
# 安裝 paper-reader 的 systemd --user 計時器（每晚 03:00 自動跑）。
# 預設專案位於 ~/CC_Paper_researcher；若 clone 到別處，請改 unit 檔的路徑。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.config/systemd/user"

mkdir -p "$DEST" "$HOME/CC_Paper_researcher/data/logs"
cp "$DIR/paper-reader.service" "$DIR/paper-reader.timer" "$DEST/"

systemctl --user daemon-reload
systemctl --user enable --now paper-reader.timer

echo "已安裝。下次觸發時間："
systemctl --user list-timers paper-reader.timer --no-pager || true
echo
echo "手動測試一次：  systemctl --user start paper-reader.service"
echo "看日誌：        journalctl --user -u paper-reader.service -f"
echo "或檔案日誌：    tail -f ~/CC_Paper_researcher/data/logs/nightly.log"
