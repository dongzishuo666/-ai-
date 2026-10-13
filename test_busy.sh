#!/bin/bash
BASE=http://127.0.0.1:8000/api

SID=$(curl -s -X POST $BASE/chat/sessions -H 'Content-Type: application/json' \
  -d '{"title":"busy测试","mode":"parallel","supervisor_enabled":false,"memory_enabled":false}' \
  | grep -o '"id":[0-9]*' | head -1 | cut -d: -f2)
echo "session_id=$SID (parallel 模式，逐个调用3个AI)"

curl -s -N --max-time 90 -X POST $BASE/chat/sessions/$SID/messages \
  -H 'Content-Type: application/json' \
  -d '{"content":"请详细论述人工智能对教育的影响，写一段800字的论述"}' > /tmp/sse_out.txt &
CURL_PID=$!

for i in $(seq 1 24); do
  busy=$(curl -s $BASE/system/ai-status | python3 -c "
import json,sys
try:
  d=json.load(sys.stdin)
  b=[(m['ai_id'],m['active_calls']) for m in d['data']['models'] if m.get('busy')]
  print(', '.join(f'{a}(x{c})' for a,c in b) if b else '')
except: print('')")
  echo "t=${i}00ms busy: ${busy:-（休息中）}"
  sleep 1
done

wait $CURL_PID 2>/dev/null
echo "--- SSE 事件摘要 ---"
grep -o 'event: [a-z_]*' /tmp/sse_out.txt | sort | uniq -c
curl -s -X DELETE $BASE/chat/sessions/$SID > /dev/null 2>&1 || true
echo DONE
