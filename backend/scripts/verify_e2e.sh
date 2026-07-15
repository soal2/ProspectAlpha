#!/usr/bin/env bash
# ProspectAlpha end-to-end verification (WP7).
#
# Prerequisites:
#   - Qdrant running:  docker run -d --name qdrant -p 6333:6333 -v qdrant_data:/qdrant/storage qdrant/qdrant
#   - backend/.env with a funded DashScope EMBEDDING_API_KEY (embedding hard-fails otherwise)
#   - Backend running: cd backend && .venv/bin/uvicorn riskagent.api.main:create_app --factory --port 8000
#
# Usage: bash backend/scripts/verify_e2e.sh [SAMPLE_PDF]

set -uo pipefail

API="${API:-http://localhost:8000}"
QDRANT="${QDRANT:-http://localhost:6333}"
SAMPLE_PDF="${1:-/Users/eversse/Documents/codes/FinancialTech/mineru_output_ysyy_full/ysyy.pdf/ocr/ysyy.pdf_origin.pdf}"

PASS=0
FAIL=0
check() { # check <label> <condition-exit-code>
  if [ "$2" -eq 0 ]; then PASS=$((PASS+1)); echo "  OK  $1"; else FAIL=$((FAIL+1)); echo "  FAIL  $1"; fi
}

echo "== 0. 服务探活 =="
curl -sf "$API/health" >/dev/null 2>&1 || curl -sf "$API/docs" >/dev/null 2>&1
check "后端可达 ($API)" $?
curl -sf "$QDRANT/collections" >/dev/null
check "Qdrant 可达 ($QDRANT)" $?

echo "== 1. 风险规则全量入库 =="
RULE_COUNT=$(curl -sf "$API/risk-rules" | python3 -c "import sys,json;d=json.load(sys.stdin);print(len(d) if isinstance(d,list) else len(d.get('rules',d.get('items',[]))))" 2>/dev/null)
[ "${RULE_COUNT:-0}" -eq 143 ]
check "GET /risk-rules 返回 143 条规则（实际 ${RULE_COUNT:-?}）" $?

echo "== 2. 技能模块 =="
SKILL_COUNT=$(curl -sf "$API/skills" | python3 -c "import sys,json;print(len(json.load(sys.stdin)))" 2>/dev/null)
[ "${SKILL_COUNT:-0}" -eq 6 ]
check "GET /skills 列出 6 个技能（实际 ${SKILL_COUNT:-?}）" $?

echo "== 3. PDF 上传解析与原文回传 =="
DOC_ID=""
if [ -f "$SAMPLE_PDF" ]; then
  UPLOAD=$(curl -sf -F "file=@$SAMPLE_PDF" "$API/documents/upload")
  DOC_ID=$(echo "$UPLOAD" | python3 -c "import sys,json;print(json.load(sys.stdin).get('document_id',''))" 2>/dev/null)
  BLOCKS=$(echo "$UPLOAD" | python3 -c "import sys,json;print(json.load(sys.stdin).get('block_count',0))" 2>/dev/null)
  [ -n "$DOC_ID" ] && [ "${BLOCKS:-0}" -gt 0 ]
  check "上传解析 block_count>0（doc=$DOC_ID blocks=${BLOCKS:-?}）" $?
  HEAD5=$(curl -sf "$API/documents/$DOC_ID/file" | head -c 5)
  [ "$HEAD5" = "%PDF-" ]
  check "GET /documents/\$DOC/file 前 5 字节为 %PDF-" $?
else
  echo "  WARN  样例 PDF 不存在（${SAMPLE_PDF}），跳过 3。"
fi

echo "== 4. 分析全流程（真实 embedding + DeepSeek，需账号可用） =="
if [ -n "$DOC_ID" ]; then
  RUN=$(curl -sf -X POST "$API/analysis-runs" -H 'Content-Type: application/json' \
    -d "{\"company_name\":\"验收测试公司\",\"document_id\":\"$DOC_ID\",\"document_text\":\"\"}")
  RUN_ID=$(echo "$RUN" | python3 -c "import sys,json;print(json.load(sys.stdin).get('run_id',''))" 2>/dev/null)
  [ -n "$RUN_ID" ]; check "创建分析 run（${RUN_ID}）" $?
  curl -sf -X POST "$API/analysis-runs/$RUN_ID/start" >/dev/null
  check "启动分析" $?
  echo "  ...  轮询状态（最长 20 分钟）"
  STATUS="running"
  for _ in $(seq 1 240); do
    STATUS=$(curl -sf "$API/analysis-runs/$RUN_ID" | python3 -c "import sys,json;print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
    case "$STATUS" in completed|failed) break;; esac
    sleep 5
  done
  [ "$STATUS" = "completed" ]; check "run 状态 completed（实际 ${STATUS}）" $?

  if [ "$STATUS" = "completed" ]; then
    DETAIL=$(curl -sf "$API/analysis-runs/$RUN_ID")
    echo "$DETAIL" | python3 - <<'PY'
import json, sys
run = json.load(sys.stdin)
events = run.get("events", [])
types = {e.get("event_type") for e in events}
ok = lambda label, cond: print(f"  {'OK ' if cond else 'FAIL'}  {label}")
ok("SSE/事件含 expert_llm_* LLM 二判事件", any(t and t.startswith("expert_llm") for t in types) or any("expert_llm" in json.dumps(e, ensure_ascii=False) for e in events))
ok("事件含 mission_brief 广播（strategy_broadcast_sent）", any("strategy_broadcast" in json.dumps(e) for e in events))
ok("事件含 peer_request → strategic_agent", any(e.get("payload", {}).get("recipient") == "strategic_agent" for e in events if isinstance(e.get("payload"), dict)))
ok("报告已生成", bool(run.get("report") or "报告" in json.dumps(events, ensure_ascii=False)))
PY

    EV=$(curl -sf "$API/analysis-runs/$RUN_ID/evidence" 2>/dev/null || echo "[]")
    echo "$EV" | python3 - <<'PY'
import json, sys
items = json.load(sys.stdin)
items = items if isinstance(items, list) else items.get("items", [])
good = [e for e in items if e.get("document_id") and e.get("page_number") and len(e.get("bbox") or []) == 4 and e.get("source_hash")]
print(f"  {'OK ' if items and len(good) == len(items) else 'FAIL'}  每条 evidence 含 document_id/页码/4元素bbox/source_hash（{len(good)}/{len(items)}）")
PY
  else
    FAILURE=$(curl -sf "$API/analysis-runs/$RUN_ID" | python3 -c "import sys,json;r=json.load(sys.stdin);print(r.get('failure_reason') or r.get('error') or '')" 2>/dev/null)
    [ -n "$FAILURE" ] && echo "  INFO  失败原因：$FAILURE"
  fi
else
  echo "  WARN  无 DOC_ID，跳过 4。"
fi

echo "== 5. 向量索引真实性 =="
COLLECTIONS=$(curl -sf --noproxy '*' "$QDRANT/collections")
TARGET=$(echo "$COLLECTIONS" | python3 -c "import sys,json;names=[c['name'] for c in json.load(sys.stdin)['result']['collections']];print(next((n for n in names if n.startswith('prospect_alpha_blocks')),''))" 2>/dev/null)
PTS=0
if [ -n "${TARGET}" ]; then
  PTS=$(curl -sf --noproxy '*' "$QDRANT/collections/$TARGET" | python3 -c "import sys,json;print(json.load(sys.stdin)['result'].get('points_count',0))" 2>/dev/null)
fi
[ -n "${TARGET}" ] && [ "${PTS:-0}" -gt 0 ]
check "Qdrant 版本化 collection 有 points（${TARGET:-无}, points=${PTS:-0}）" $?

echo "== 6. 报告知识库 =="
KB=$(curl -sf "$API/knowledge/reports" | python3 -c "import sys,json;print(len(json.load(sys.stdin)))" 2>/dev/null)
[ "${KB:-0}" -ge 1 ]
check "GET /knowledge/reports 有归档报告（${KB:-0} 份）" $?

echo
echo "== 结果：$PASS 通过 / $FAIL 失败 =="
[ "$FAIL" -eq 0 ]
