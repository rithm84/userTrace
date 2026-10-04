#!/bin/zsh
# Rebuild one character in place in the ACTIVE Rive file (must be named utrace-agent-<name>), export, capture, and write sheets.
set -e
NAME=$1; STATES=${2:-"thinking,ship,reconnecting,building,listening"}
SCR=$(cd "$(dirname "$0")" && pwd)
cd $SCR && python3 -c "
from mcp import call
s=call('session_info', {}); print('active:', s['activeFileName']); assert s['activeFileName']=='utrace-agent-$NAME', s['activeFileName']" || exit 1
python3 build_character.py $NAME 2>&1 | tail -1
rm -rf shots/seq-$NAME && node rive-seq.mjs ../../../../packages/utrace-agent-character/assets/utrace-agent-$NAME.riv shots/seq-$NAME "$STATES" 2>&1 | grep -c frames
python3 seq_sheets.py shots/seq-$NAME shots/seq-$NAME
