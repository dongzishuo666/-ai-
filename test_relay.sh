#!/bin/bash
KEY="sk-w4E6MGQk1tE2lUqC2f684b0e79Fd47B7A92c54FaF801CfDa"
for i in 1 2 3; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 40 -X POST https://api.apiyi.com/v1/chat/completions \
    -H 'Content-Type: application/json' -H "Authorization: Bearer $KEY" \
    -d '{"model":"gemini-2.5-flash","messages":[{"role":"user","content":"hi"}],"max_tokens":5}')
  echo "gemini-2.5-flash 第${i}次 => HTTP $code"
done
for m in gemini-2.5-flash-lite gemini-3-flash-preview gemini-3.5-flash; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 40 -X POST https://api.apiyi.com/v1/chat/completions \
    -H 'Content-Type: application/json' -H "Authorization: Bearer $KEY" \
    -d "{\"model\":\"$m\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":5}")
  echo "$m => HTTP $code"
done
