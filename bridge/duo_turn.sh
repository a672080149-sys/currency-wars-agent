#!/bin/bash
# 双人模式一回合的准备：捡球 → 看商店 → 收起商店 → DS 提议（附上商店内容）
# 用法：bash duo_turn.sh "给 DS 的补充说明"
cd "$(dirname "$0")"
PY="../vendor/March7thAssistant/.venv/Scripts/python.exe"
$PY -X utf8 cw.py collect >/dev/null 2>&1
page=$($PY -X utf8 cw.py look | head -1)
shop=""
if [[ "$page" != \[商店\]* ]] && [[ "$page" != \[* ]]; then
  $PY -X utf8 cw.py tap x=1630 y=990 >/dev/null; sleep 1.2
fi
first=$($PY -X utf8 cw.py look)
if [[ "$first" == \[商店\]* ]]; then
  shop="$first"
  $PY -X utf8 cw.py tap x=1630 y=990 >/dev/null; sleep 1
fi
note="$1"
[ -n "$shop" ] && note="$note
商店（已收起，买卡要先 open_shop）：
$shop"
$PY -X utf8 duo.py propose --note "$note"
