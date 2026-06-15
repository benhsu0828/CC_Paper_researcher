#!/usr/bin/env bash
# 安裝 paper-reader 的 systemd --user 計時器（每晚 03:00 自動跑）。
# 預設專案位於 ~/CC_Paper_researcher；若 clone 到別處，請改 unit 檔的路徑。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.config/systemd/user"

mkdir -p "$DEST" "$HOME/CC_Paper_researcher/data/logs"
cp "$DIR/paper-reader.service" "$DIR/paper-reader.timer" \
   "$DIR/paper-reader-bot.service" "$DEST/"

systemctl --user daemon-reload
systemctl --user enable --now paper-reader.timer
systemctl --user enable --now paper-reader-bot.service   # 常駐 Discord 監聽 bot

echo "已安裝。下次夜跑觸發時間："
systemctl --user list-timers paper-reader.timer --no-pager || true
echo
echo "Discord bot 狀態："
systemctl --user status paper-reader-bot.service --no-pager | head -n 5 || true
echo
echo "夜跑手動測試：  systemctl --user start paper-reader.service"
echo "夜跑日誌：      journalctl --user -u paper-reader.service -f"
echo "bot 日誌：      tail -f ~/CC_Paper_researcher/data/logs/bot.log"
echo "重啟 bot：      systemctl --user restart paper-reader-bot.service"
echo
echo "用法：在 Discord 頻道輸入  !read   /  !read 3  /  !read <arxiv_id>  /  !status"
