#!/bin/bash
# M9 soak monitor: every INTERVAL seconds, log memory of the app's processes, disk use and clip counts.
#   tools/monitor.sh [interval_seconds] >> data/monitor.log
# Stops when the app (pid in data/app.pid) exits.
cd "$(dirname "$0")/.."
INTERVAL=${1:-300}
APP=$(cat data/app.pid)
echo "time      app_mb worker_mb rec_procs rec_mb data_mb clips errors restarts"
while kill -0 "$APP" 2>/dev/null; do
  app_kb=$(ps -o rss= -p "$APP" | tr -d ' ')
  kids=$(pgrep -P "$APP" | tr '\n' ',' | sed 's/,$//')
  worker_kb=$(ps -o rss=,command= -p "${kids:-0}" 2>/dev/null | grep multiprocessing-fork | awk '{s+=$1} END {print s+0}')
  rec=$(ps -ax -o rss=,command= | grep -E "streamlink --twitch|ffmpeg -hide_banner -loglevel warning -nostdin -i pipe:0" | grep -v grep)
  rec_n=$(printf "%s\n" "$rec" | grep -c . )
  rec_kb=$(printf "%s\n" "$rec" | awk '{s+=$1} END {print s+0}')
  data_mb=$(du -sm data 2>/dev/null | awk '{print $1}')
  clips=$(find clips -name meta.json 2>/dev/null | wc -l | tr -d ' ')
  errors=$(grep -cE "ERROR|died|crashed|Traceback" data/clipper.log)
  restarts=$(grep -c "pipeline exited" data/clipper.log)
  printf "%s %6d %9d %9d %6d %7d %5d %6d %8d\n" "$(date +%H:%M:%S)" $((app_kb/1024)) $((worker_kb/1024)) \
    "$rec_n" $((rec_kb/1024)) "$data_mb" "$clips" "$errors" "$restarts"
  sleep "$INTERVAL"
done
echo "$(date +%H:%M:%S) app exited"
