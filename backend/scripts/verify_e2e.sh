#!/usr/bin/env bash
# PostgreSQL-only document RAG smoke test.
# Usage: API=http://127.0.0.1:8000 bash scripts/verify_e2e.sh /path/to/sample.pdf

set -uo pipefail

API="${API:-http://127.0.0.1:8000}"
SAMPLE_PDF="${1:-}"

if [ ! -f "$SAMPLE_PDF" ]; then
  echo "Usage: $0 /path/to/sample.pdf" >&2
  exit 2
fi

curl -fsS "$API/health" >/dev/null
UPLOAD=$(curl -fsS -F "file=@$SAMPLE_PDF;type=application/pdf" "$API/documents/upload")
DOC_ID=$(echo "$UPLOAD" | python3 -c "import json,sys; print(json.load(sys.stdin)['document_id'])")
BLOCKS=$(echo "$UPLOAD" | python3 -c "import json,sys; print(json.load(sys.stdin)['block_count'])")

if [ -z "$DOC_ID" ] || [ "$BLOCKS" -le 0 ]; then
  echo "Upload did not produce searchable blocks" >&2
  exit 1
fi

ANSWER=$(curl -fsS -N -X POST "$API/documents/$DOC_ID/ask" \
  -H 'Content-Type: application/json' \
  -d '{"question":"请概括这份招股书披露的主要风险。","top_k":4}')

echo "$ANSWER" | grep -q '"type": "done"'
echo "$ANSWER" | grep -q "\"document_id\": \"$DOC_ID\""
echo "$ANSWER" | grep -q '"bbox_regions"'

echo "OK document=$DOC_ID blocks=$BLOCKS PostgreSQL RAG answer completed"
