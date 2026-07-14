"""SQLAlchemy ORM models for production runtime persistence."""

from __future__ import annotations

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from riskagent.database import Base


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


class RetrievalIndexRow(Base):
    __tablename__ = "retrieval_index"

    index_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    document_id: Mapped[str] = mapped_column(String(64), index=True)
    block_id: Mapped[str] = mapped_column(String(128), index=True)
    text: Mapped[str] = mapped_column(Text)
    terms: Mapped[list[str]] = mapped_column(JSON)
    embedding: Mapped[list[float]] = mapped_column(JSON)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
