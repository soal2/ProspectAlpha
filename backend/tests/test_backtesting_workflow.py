from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from riskagent.api import backtests as backtests_api
from riskagent.api.main import create_app
from riskagent.backtesting.service import BacktestCreate, BacktestService, MarketImportRequest, SCORECARD_VERSION
from riskagent.database import Base
from riskagent.models import RiskScoreSnapshotRow


def test_published_snapshot_requires_pre_listing_score_and_generates_audit_artifact(tmp_path: Path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    service = BacktestService(session_factory=factory, initialize=False)
    security, company, prices = (tmp_path / "security.csv", tmp_path / "company.csv", tmp_path / "prices.csv")
    security.write_text("S_INFO_WINDCODE,S_INFO_COMPCODE,S_INFO_NAME,S_INFO_LISTDATE,S_INFO_LISTPRICE\n0001.HK,C1,测试,20270102,10\n", encoding="utf-8")
    company.write_text("S_INFO_COMPCODE,S_INFO_COMPNAME\nC1,测试\n", encoding="utf-8")
    prices.write_text("S_INFO_WINDCODE,TRADE_DT,S_DQ_LOW,S_DQ_CLOSE\n0001.HK,20270102,10,10\n0001.HK,20270103,9,9\n0001.HK,20270104,8,9\n0001.HK,20270105,9,9\n0001.HK,20270106,9,9\n", encoding="utf-8")
    imported = service.import_market_data(MarketImportRequest(security_csv=str(security), company_csv=str(company), prices_csv=str(prices)))
    with factory() as session:
        session.add(RiskScoreSnapshotRow(score_id="score_ok", run_id="run_1", wind_code="0001.HK", scorecard_version=SCORECARD_VERSION, total_score=80, risk_level="very_high", breakdown={"D1": 15, "D2": 30, "D3": 35, "EXT": 0, "baseline_d1": 100, "without_d2": 50, "without_d3": 45, "without_ext": 80}, limitations=[], analysis_as_of_date="20261231", input_hash="a" * 64, evidence_hash="b" * 64, finding_snapshot=[{"title": "文本粉饰风险", "origin": "text_signal", "dimension_code": "D3", "severity": "high"}], industry_profile={"code": "BIOPHARMA", "name": "生物医药"}, text_signal_snapshot={}, weight_profile={"D1": 15, "D2": 30, "D3": 35, "EXT": 20}, research_snapshot_ids=[], created_at="2026-12-31T00:00:00+00:00"))
        session.commit()
    service.publish_market_snapshot(imported["snapshot_id"])
    assert service.preview_candidates(imported["snapshot_id"])[0].windCode == "0001.HK"
    draft = service.create_backtest(BacktestCreate(snapshot_id=imported["snapshot_id"], wind_code="0001.HK"))
    job = service.start_backtest(draft.id)
    service.run_job(job.id)
    detail = service.detail(draft.id)
    assert detail["summary"]["status"] == "完成", detail["jobs"][0]["error"]
    assert len(detail["visuals"]["roc"]) >= 2
    assert len(detail["visuals"]["lift"]) == 6
    assert detail["artifact"]
    artifact = Path(detail["artifact"]).read_text(encoding="utf-8")
    assert "公司级风险—行情回放" in artifact
    assert "核心指标" in artifact


def test_audit_artifact_previews_and_downloads_html(monkeypatch, tmp_path: Path):
    artifact = tmp_path / "audit.html"
    artifact.write_text("<!doctype html><html><body><h1>审计报告</h1></body></html>", encoding="utf-8")
    monkeypatch.setattr(backtests_api.service, "detail", lambda _id: {"artifact": str(artifact)})
    client = TestClient(create_app())

    preview = client.get("/backtests/bt_test/artifact")
    download = client.get("/backtests/bt_test/artifact?download=1")

    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("text/html")
    assert preview.headers["content-disposition"].startswith("inline")
    assert preview.headers["x-content-type-options"] == "nosniff"
    assert preview.text == artifact.read_text(encoding="utf-8")
    assert download.headers["content-disposition"].startswith("attachment")


def test_hidden_first_score_uses_fixed_weights_and_ignores_confidence():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    service = BacktestService(session_factory=sessionmaker(bind=engine, expire_on_commit=False), initialize=False)
    findings = [
        {"dimension_code": "D1", "severity": "high", "evidence_grade": "direct", "confidence": 0.01},
        {"dimension_code": "D2", "severity": "high", "evidence_grade": "direct", "confidence": 0.01},
        {"dimension_code": "D3", "severity": "high", "evidence_grade": "direct", "confidence": 0.01},
        {"dimension_code": "EXT", "severity": "low", "evidence_grade": "lead", "confidence": 0.01, "hypothesis_status": "corroborated"},
    ]
    low = service.score(run_id="low", wind_code="1", findings=findings)
    high = service.score(run_id="high", wind_code="2", findings=[{**item, "confidence": 0.99} for item in findings])

    assert low["score"] == high["score"]
    assert low["breakdown"] == high["breakdown"]
    assert low["weight_profile"] == {"D1": 15.0, "D2": 30.0, "D3": 35.0, "EXT": 20.0}


def test_score_caps_each_bucket_at_top_three_findings():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    service = BacktestService(session_factory=sessionmaker(bind=engine, expire_on_commit=False), initialize=False)
    result = service.score(
        run_id="caps",
        wind_code="3",
        findings=[{"dimension_code": "D3", "severity": "high", "evidence_grade": "direct"} for _ in range(8)],
    )

    assert result["breakdown"]["D3"] == 35.0
    assert result["score"] == 35.0
