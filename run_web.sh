#!/bin/bash
# video2text Web 管理面板启动脚本
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
echo "🎬 video2text Web 面板"
echo "   端口: 15801"
echo "   打开: http://127.0.0.1:15801"
echo ""
python3 web/app.py
