"""Data ingestion boundary for prospectus PDFs, company metadata, and market data."""

from riskagent.ingestion.prospectus import (
    ProspectusIngestionRepository,
    ProspectusRegisterRequest,
    ProspectusRegistration,
)

__all__ = [
    "ProspectusIngestionRepository",
    "ProspectusRegisterRequest",
    "ProspectusRegistration",
]
