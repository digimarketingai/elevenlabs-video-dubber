#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "Installing FFmpeg and fonts / 安裝 FFmpeg 與中文字型"
apt-get update -qq
apt-get install -y -qq ffmpeg fonts-noto-cjk

echo "Installing Python dependencies / 安裝 Python 套件"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo
echo "Starting app / 啟動應用程式"
echo "Open the Gradio share link below."
echo "請開啟下方的 Gradio 分享連結。"
echo "Keep this cell running / 請保持此儲存格執行。"
echo

exec python -u app.py --share
