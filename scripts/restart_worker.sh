#!/bin/zsh
# Restart the live ingest worker and verify it. Usage: ./scripts/restart_worker.sh [session_id]
# 1. stops /tmp/run_xqc.py  2. relaunches detached (setsid, survives this shell)
# 3. verifies via ps  4. waits for a NEW done window before printing success.
set -u
PROJ="/Users/purplesprite/twitch-stream-summarizer"
SID="${1:-1}"
DB="$PROJ/data/app.db"
LOG="/tmp/worker.log"

max_done() {
  python3 -c "import sqlite3; db=sqlite3.connect('$DB'); print(db.execute('select max(id) from windows where session_id=$SID and status=\"done\"').fetchone()[0] or 0)"
}

BEFORE=$(max_done)
echo "pre-restart max done window: $BEFORE"
pkill -f "/tmp/run_xqc.py" && echo "worker stopped" || echo "no worker was running"
sleep 3
if pgrep -f "/tmp/run_xqc.py" >/dev/null; then echo "FATAL: worker still alive after pkill"; exit 1; fi
cd "$PROJ"
nohup env PYTHONPATH="$PROJ" python3 /tmp/run_xqc.py > "$LOG" 2>&1 < /dev/null &
disown
sleep 5
PID=$(pgrep -f "/tmp/run_xqc.py" | head -1)
if [[ -z "$PID" ]]; then echo "FATAL: worker not in ps after launch"; tail -20 "$LOG"; exit 1; fi
echo "worker pid $PID in ps"
if grep -qiE "traceback" "$LOG"; then echo "FATAL: traceback in log"; tail -20 "$LOG"; exit 1; fi
echo "waiting for a new done window (timeout 12m)..."
for i in $(seq 1 24); do
  sleep 30
  NOW=$(max_done)
  if [[ "$NOW" -gt "$BEFORE" ]]; then echo "SUCCESS: new done window $NOW (was $BEFORE)"; exit 0; fi
  pgrep -f "/tmp/run_xqc.py" >/dev/null || { echo "FATAL: worker died while waiting"; tail -20 "$LOG"; exit 1; }
done
echo "FATAL: no new done window within 12m (worker alive, stream may be idle)"
exit 1
