#!/usr/bin/env python3
"""Batch analysis runner for HK IPO prospectuses.

For each stock code: upload PDF -> wait for MinerU parsing to finish ->
create deep analysis run -> start -> poll to completion -> verify frozen score.
If a run fails, retry up to RETRY_COUNT times.

Usage:
    python backend/scripts/run_batch_analysis.py --batch 1
    python backend/scripts/run_batch_analysis.py --batch 2
    python backend/scripts/run_batch_analysis.py --batch 3
    python backend/scripts/run_batch_analysis.py --batch 4
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import time
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("batch_analysis")

API_BASE = os.environ.get("API_BASE", "http://localhost:8000")
PDF_DIR = Path("/Users/eversse/Documents/codes/FinancialTech/ProspectAlpha/2025(1)")
SNAPSHOT_ID = "mkt_3683a02baf124a8f"
POLL_INTERVAL = 15
POLL_TIMEOUT = 2400
DOC_WAIT_INTERVAL = 15
DOC_WAIT_TIMEOUT = 2400
RETRY_COUNT = 2
NO_PROXY = {"http": None, "https": None}
SESSION = requests.Session()
SESSION.trust_env = False
SESSION.proxies.update(NO_PROXY)

BATCHES = {
    1: [
        "2589.HK", "1364.HK", "2561.HK", "2569.HK", "2625.HK",
        "2610.HK", "2097.HK", "1276.HK", "2629.HK", "2619.HK",
    ],
    2: [
        "2603.HK", "2621.HK", "2573.HK", "2605.HK", "2609.HK",
        "2050.HK", "1828.HK", "1304.HK", "2590.HK", "2597.HK",
    ],
    3: [
        "2508.HK", "2617.HK", "1333.HK", "2580.HK", "2543.HK",
        "2525.HK", "2591.HK", "2259.HK", "2583.HK", "2595.HK",
    ],
    4: [
        "2579.HK", "2575.HK", "2026.HK", "2546.HK", "2396.HK",
        "2408.HK", "2581.HK", "2615.HK", "2513.HK", "800.HK",
        "100.HK", "1384.HK", "699.HK", "2627.HK", "2630.HK", "1641.HK",
    ],
}


def _api(method: str, path: str, **kwargs):
    url = f"{API_BASE}{path}"
    kwargs.setdefault("timeout", 60)
    r = SESSION.request(method, url, **kwargs)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code} for {method} {path}: {r.text[:300]}")
    if not r.content:
        return {}
    return r.json()


def resolve_pdf(stock_code: str) -> Path | None:
    numeric = stock_code.replace(".HK", "")
    prefix = numeric.zfill(5)
    matches = sorted(PDF_DIR.glob(f"{prefix}_*.pdf"))
    return matches[0] if matches else None


def upload_document(pdf_path: Path) -> str:
    log.info("Uploading %s", pdf_path.name)
    with pdf_path.open("rb") as fh:
        files = {"file": (pdf_path.name, fh, "application/pdf")}
        data = {"source_url": ""}
        payload = _api("POST", "/documents/upload", files=files, data=data)
    doc_id = payload.get("document_id") or payload.get("id")
    if not doc_id:
        raise RuntimeError(f"No document_id in response: {payload}")
    log.info("Uploaded document_id=%s", doc_id)
    return doc_id


def wait_for_document(doc_id: str) -> None:
    log.info("Waiting for document %s to finish parsing ...", doc_id)
    last_status = None
    deadline = time.time() + DOC_WAIT_TIMEOUT
    while time.time() < deadline:
        try:
            payload = _api("GET", f"/documents/{doc_id}")
            status = payload.get("processing_status") or payload.get("status", "unknown")
        except Exception:
            status = last_status or "unknown"
        if status != last_status:
            log.info("  document=%s status=%s", doc_id, status)
            last_status = status
        if status in {"ready", "indexed"}:
            log.info("Document %s ready (status=%s, block_count=%s)", doc_id, status, payload.get("block_count", 0))
            return
        if status in {"failed", "interrupted"}:
            raise RuntimeError(f"Document parsing failed: {status}")
        time.sleep(DOC_WAIT_INTERVAL)
    raise TimeoutError(f"Document {doc_id} did not finish parsing in {DOC_WAIT_TIMEOUT}s")


def create_analysis_run(stock_code: str, document_id: str) -> str:
    body = {
        "company_name": stock_code.replace(".HK", ""),
        "stock_code": stock_code,
        "document_id": document_id,
        "analysis_depth": "deep",
        "confirm_defaults": True,
    }
    log.info("Creating analysis run for %s", stock_code)
    payload = _api("POST", "/analysis-runs", json=body)
    run_id = payload.get("run_id")
    if not run_id:
        raise RuntimeError(f"No run_id in response: {payload}")
    log.info("Created run_id=%s", run_id)
    return run_id


def start_run(run_id: str) -> None:
    log.info("Starting run %s", run_id)
    _api("POST", f"/analysis-runs/{run_id}/start")


def wait_for_completion(run_id: str) -> dict:
    log.info("Polling run %s ...", run_id)
    last_status = None
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        payload = _api("GET", f"/analysis-runs/{run_id}")
        status = payload.get("status", "unknown")
        if status != last_status:
            log.info("  run=%s status=%s", run_id, status)
            last_status = status
        if status == "completed":
            return payload
        if status == "failed":
            error = payload.get("error") or payload.get("failure_reason") or "unknown"
            raise RuntimeError(f"Run failed: {error}")
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"Run {run_id} did not complete in {POLL_TIMEOUT}s")


def verify_frozen_score(stock_code: str) -> dict:
    cmd = [
        "docker", "exec", "backend-postgres-1", "psql",
        "-U", "prospect_alpha", "-d", "prospect_alpha", "-t", "-A",
        "-c",
        f"SELECT total_score, risk_level FROM risk_score_snapshots "
        f"WHERE wind_code='{stock_code}' ORDER BY created_at DESC LIMIT 1;",
    ]
    raw = subprocess.check_output(cmd, text=True).strip()
    if not raw:
        return {}
    parts = raw.split("|")
    if len(parts) < 2:
        return {}
    try:
        return {"total_score": float(parts[0]), "risk_level": parts[1]}
    except ValueError:
        return {}


def run_backtest() -> dict:
    log.info("Running backtest snapshot=%s", SNAPSHOT_ID)
    payload = _api("POST", "/backtests", json={"snapshot_id": SNAPSHOT_ID})
    log.info(
        "Backtest done: id=%s auc=%s ks=%s lift=%s samples=%s",
        payload.get("id"),
        payload.get("auc"),
        payload.get("ks"),
        payload.get("lift10"),
        payload.get("samples"),
    )
    return payload


def process_stock(stock_code: str) -> dict:
    pdf_path = resolve_pdf(stock_code)
    if pdf_path is None:
        return {
            "stock_code": stock_code,
            "status": "skipped",
            "error": "PDF not found",
        }

    last_err = None
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            doc_id = upload_document(pdf_path)
            wait_for_document(doc_id)
            run_id = create_analysis_run(stock_code, doc_id)
            start_run(run_id)
            wait_for_completion(run_id)
            score = verify_frozen_score(stock_code)
            if not score:
                raise RuntimeError("No frozen score found in risk_score_snapshots")

            total = score.get("total_score", 0)
            level = score.get("risk_level", "")
            if total == 0 and level == "very_low":
                log.warning(
                    "Stock %s has score=0 very_low; assuming zero-findings result",
                    stock_code,
                )

            log.info(
                "SUCCESS %s run=%s score=%s level=%s",
                stock_code,
                run_id,
                total,
                level,
            )
            return {
                "stock_code": stock_code,
                "status": "ok",
                "run_id": run_id,
                "document_id": doc_id,
                **score,
            }
        except Exception as exc:  # pylint: disable=broad-except
            last_err = exc
            log.warning("Attempt %s/%s failed for %s: %s", attempt, RETRY_COUNT, stock_code, exc)
            time.sleep(10)

    return {
        "stock_code": stock_code,
        "status": "failed",
        "error": str(last_err),
    }


def process_batch(batch: list[str]) -> None:
    results_path = Path("/tmp/batch_results.json")
    results: list[dict] = []

    for idx, code in enumerate(batch, 1):
        log.info("===== Batch %s: %d/%d (%s) =====", "current", idx, len(batch), code)
        res = process_stock(code)
        results.append(res)
        results_path.write_text(json.dumps(results, ensure_ascii=False, indent=2))

    log.info("Batch finished. Results saved to %s", results_path)
    bt = run_backtest()
    bt_path = Path("/tmp/latest_backtest.json")
    bt_path.write_text(json.dumps(bt, ensure_ascii=False, indent=2))
    log.info("Backtest result saved to %s", bt_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch run deep analysis for HK IPO prospectuses.")
    parser.add_argument("--batch", type=int, required=True, choices=[1, 2, 3, 4], help="Batch number")
    args = parser.parse_args()

    batch = BATCHES[args.batch]
    log.info("Starting batch %d: %s", args.batch, batch)
    process_batch(batch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
