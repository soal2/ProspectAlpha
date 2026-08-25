"""SQLAlchemy ORM models for production runtime persistence."""

from __future__ import annotations

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from riskagent.database import Base


class ProspectusDocumentRow(Base):
    __tablename__ = "prospectus_documents"

    document_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    doc_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    original_filename: Mapped[str] = mapped_column(String(512))
    raw_pdf_path: Mapped[str] = mapped_column(String(1024))
    content_type: Mapped[str] = mapped_column(String(128), default="application/pdf")
    file_sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    file_size_bytes: Mapped[int] = mapped_column(Integer)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="registered", index=True)
    processing_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    processing_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_attempts: Mapped[int] = mapped_column(Integer, default=0)
    processing_started_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processing_heartbeat_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processing_finished_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentAssetRow(Base):
    __tablename__ = "documents"

    document_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(128))
    file_sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    storage_uri: Mapped[str] = mapped_column(String(1024))
    file_size_bytes: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int] = mapped_column(Integer)
    text_chars: Mapped[int] = mapped_column(Integer)
    block_count: Mapped[int] = mapped_column(Integer)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())

    blocks: Mapped[list["DocumentBlockRow"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class DocumentBlockRow(Base):
    __tablename__ = "document_blocks"

    block_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.document_id"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    section_path: Mapped[str] = mapped_column(String(512))
    block_type: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    bbox: Mapped[list[float]] = mapped_column(JSON)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    source_hash: Mapped[str] = mapped_column(String(64), index=True)
    parser_version: Mapped[str] = mapped_column(String(128))

    document: Mapped[DocumentAssetRow] = relationship(back_populates="blocks")


class AnalysisRunRow(Base):
    __tablename__ = "analysis_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_name: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(64), index=True)
    current_stage: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[str] = mapped_column(String(64))
    completed_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data_cutoff_date: Mapped[str] = mapped_column(String(32))
    inputs_frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    payload: Mapped[dict] = mapped_column(JSON)


class EvidenceLibraryRow(Base):
    __tablename__ = "evidence_library"

    dedupe_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_run_id: Mapped[str] = mapped_column(String(64), index=True)
    document_block_id: Mapped[str] = mapped_column(String(128), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    section_path: Mapped[str] = mapped_column(String(512))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(64))


class ReportLibraryRow(Base):
    __tablename__ = "report_library"

    report_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    company_name: Mapped[str] = mapped_column(String(512), index=True)
    document_id: Mapped[str] = mapped_column(String(64), index=True)
    rule_set_id: Mapped[str] = mapped_column(String(64))
    overall_risk_level: Mapped[str] = mapped_column(String(32))
    finding_count: Mapped[int] = mapped_column(Integer)
    markdown: Mapped[str] = mapped_column(Text)
    sections: Mapped[list[dict]] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(64))


class AgentExpertRow(Base):
    __tablename__ = "agent_experts"

    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(256))
    layer: Mapped[str] = mapped_column(String(16), index=True)
    description: Mapped[str] = mapped_column(Text)
    expertise: Mapped[list[str]] = mapped_column(JSON)
    call_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_tokens: Mapped[int] = mapped_column(Integer, default=0)
    avatar: Mapped[str] = mapped_column(String(16))
    accent: Mapped[str] = mapped_column(String(16))
    display_order: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentSpecRow(Base):
    __tablename__ = "agent_specs"

    agent_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    persona_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    layer: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    function: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str] = mapped_column(String(256))
    rule_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    categories: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    display_order: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RiskRuleSetRow(Base):
    __tablename__ = "risk_rule_sets"

    rule_set_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(256))
    source_workbook: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_main_sheet: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source_summary_sheet: Mapped[str | None] = mapped_column(String(256), nullable=True)
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage_notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    display_order: Mapped[int] = mapped_column(Integer, default=1, index=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RiskRuleDimensionRow(Base):
    __tablename__ = "risk_rule_dimensions"

    dimension_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    rule_set_id: Mapped[str] = mapped_column(ForeignKey("risk_rule_sets.rule_set_id"), index=True)
    dimension_code: Mapped[str] = mapped_column(String(64), index=True)
    dimension: Mapped[str] = mapped_column(String(256))
    rule_type: Mapped[str] = mapped_column(String(128), index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RiskRuleRow(Base):
    __tablename__ = "risk_rules"

    rule_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    rule_set_id: Mapped[str] = mapped_column(ForeignKey("risk_rule_sets.rule_set_id"), index=True)
    dimension_code: Mapped[str] = mapped_column(String(64), index=True)
    dimension: Mapped[str] = mapped_column(String(256))
    rule_type: Mapped[str] = mapped_column(String(128), index=True)
    category: Mapped[str] = mapped_column(String(256), index=True)
    risk_item: Mapped[str] = mapped_column(String(512))
    description: Mapped[str] = mapped_column(Text)
    trigger_condition: Mapped[str] = mapped_column(Text)
    calculation_or_method: Mapped[str] = mapped_column(Text)
    source_sections: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    display_order: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MarketDatasetSnapshotRow(Base):
    __tablename__ = "market_dataset_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    file_hashes: Mapped[dict] = mapped_column(JSON)
    source_paths: Mapped[dict] = mapped_column(JSON)
    coverage: Mapped[dict] = mapped_column(JSON)
    row_counts: Mapped[dict] = mapped_column(JSON)
    quality_report: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    published_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[str] = mapped_column(String(64))


class SecurityMasterRow(Base):
    __tablename__ = "security_master"
    __table_args__ = (UniqueConstraint("snapshot_id", "wind_code", name="uq_security_snapshot_code"),)

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("market_dataset_snapshots.snapshot_id"), index=True)
    wind_code: Mapped[str] = mapped_column(String(32), index=True)
    company_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    listing_date: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    listing_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)


class CompanyInfoRow(Base):
    __tablename__ = "company_info"
    __table_args__ = (UniqueConstraint("snapshot_id", "company_code", name="uq_company_snapshot_code"),)

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("market_dataset_snapshots.snapshot_id"), index=True)
    company_code: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    english_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    business_scope: Mapped[str | None] = mapped_column(Text, nullable=True)


class DailyPriceRow(Base):
    __tablename__ = "daily_prices"
    __table_args__ = (UniqueConstraint("snapshot_id", "wind_code", "trade_date", name="uq_price_snapshot_code_date"),)

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("market_dataset_snapshots.snapshot_id"), index=True)
    wind_code: Mapped[str] = mapped_column(String(32), index=True)
    trade_date: Mapped[str] = mapped_column(String(16), index=True)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    open: Mapped[float | None] = mapped_column(Float, nullable=True)
    high: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)


class IpoOutcomeLabelRow(Base):
    __tablename__ = "ipo_outcome_labels"
    __table_args__ = (UniqueConstraint("snapshot_id", "wind_code", "horizon", name="uq_label_snapshot_code_horizon"),)

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("market_dataset_snapshots.snapshot_id"), index=True)
    wind_code: Mapped[str] = mapped_column(String(32), index=True)
    horizon: Mapped[int] = mapped_column(Integer)
    offer_price: Mapped[float] = mapped_column(Float)
    end_close: Mapped[float] = mapped_column(Float)
    min_low: Mapped[float] = mapped_column(Float)
    return_pct: Mapped[float] = mapped_column(Float)
    max_drawdown_pct: Mapped[float] = mapped_column(Float)
    significant_decline: Mapped[bool] = mapped_column(Boolean)
    breaking: Mapped[bool] = mapped_column(Boolean)


class RiskScoreSnapshotRow(Base):
    __tablename__ = "risk_score_snapshots"

    score_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    wind_code: Mapped[str] = mapped_column(String(32), index=True)
    scorecard_version: Mapped[str] = mapped_column(String(32))
    total_score: Mapped[float] = mapped_column(Float)
    risk_level: Mapped[str] = mapped_column(String(16))
    breakdown: Mapped[dict] = mapped_column(JSON)
    limitations: Mapped[list[str]] = mapped_column(JSON)
    analysis_as_of_date: Mapped[str] = mapped_column(String(16), index=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    evidence_hash: Mapped[str] = mapped_column(String(64))
    finding_snapshot: Mapped[list[dict]] = mapped_column(JSON, default=list)
    industry_profile: Mapped[dict] = mapped_column(JSON, default=dict)
    text_signal_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    weight_profile: Mapped[dict] = mapped_column(JSON, default=dict)
    research_snapshot_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[str] = mapped_column(String(64))


class BacktestRunRow(Base):
    __tablename__ = "backtest_runs"

    backtest_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("market_dataset_snapshots.snapshot_id"), index=True)
    name: Mapped[str] = mapped_column(String(256))
    test_start_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    test_end_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(24))
    metrics: Mapped[dict] = mapped_column(JSON)
    protocol: Mapped[dict] = mapped_column(JSON, default=dict)
    candidate_set_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    artifact_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[str] = mapped_column(String(64))


class BacktestSampleRow(Base):
    __tablename__ = "backtest_samples"
    __table_args__ = (UniqueConstraint("backtest_id", "wind_code", name="uq_backtest_sample_code"),)

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    backtest_id: Mapped[str] = mapped_column(ForeignKey("backtest_runs.backtest_id"), index=True)
    wind_code: Mapped[str] = mapped_column(String(32), index=True)
    company_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    score: Mapped[float] = mapped_column(Float)
    risk_level: Mapped[str] = mapped_column(String(16))
    actual_decline: Mapped[bool] = mapped_column(Boolean)
    actual_breaking: Mapped[bool] = mapped_column(Boolean)
    return_pct: Mapped[float] = mapped_column(Float)
    max_drawdown_pct: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)
    score_id: Mapped[str] = mapped_column(String(96), index=True)
    analysis_run_id: Mapped[str] = mapped_column(String(64), index=True)
    analysis_as_of_date: Mapped[str] = mapped_column(String(16))
    industry_profile: Mapped[dict] = mapped_column(JSON, default=dict)
    specific_risks: Mapped[list[dict]] = mapped_column(JSON, default=list)
    outcome_profile: Mapped[dict] = mapped_column(JSON, default=dict)
    retrospective_attribution: Mapped[dict] = mapped_column(JSON, default=dict)


class BacktestCandidateSetRow(Base):
    __tablename__ = "backtest_candidate_sets"

    candidate_set_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    backtest_id: Mapped[str] = mapped_column(ForeignKey("backtest_runs.backtest_id"), unique=True, index=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("market_dataset_snapshots.snapshot_id"), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    created_at: Mapped[str] = mapped_column(String(64))


class BacktestCandidateSampleRow(Base):
    __tablename__ = "backtest_candidate_samples"
    __table_args__ = (UniqueConstraint("candidate_set_id", "wind_code", name="uq_candidate_set_code"),)

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    candidate_set_id: Mapped[str] = mapped_column(ForeignKey("backtest_candidate_sets.candidate_set_id"), index=True)
    wind_code: Mapped[str] = mapped_column(String(32), index=True)
    listing_date: Mapped[str] = mapped_column(String(16))
    label_id: Mapped[str] = mapped_column(String(160))
    score_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    source_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    inclusion_status: Mapped[str] = mapped_column(String(24), default="proposed")
    exclusion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class BacktestJobRow(Base):
    __tablename__ = "backtest_jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    backtest_id: Mapped[str] = mapped_column(ForeignKey("backtest_runs.backtest_id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(128), default="queued")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64))
    completed_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
