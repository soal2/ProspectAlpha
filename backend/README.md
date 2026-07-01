# ProspectAlpha Backend

Backend skeleton for the HK-IPO RiskAgent pipeline.

This directory is intentionally structure-first. It defines module boundaries for the
data, evidence, agent, prediction, backtesting, reporting, and API layers, but it does
not implement business functionality yet.

## Module Map

- `configs/`: risk taxonomy, feature rules, agent graph, and model configuration.
- `data/`: local development data layout. Do not commit private or generated datasets.
- `src/riskagent/ingestion/`: prospectus, company metadata, and market data ingestion.
- `src/riskagent/parsing/`: PDF parsing, section detection, and table extraction.
- `src/riskagent/retrieval/`: evidence store, full-text search, vector search, and hybrid retrieval.
- `src/riskagent/features/`: financial, operating, legal, and market risk features.
- `src/riskagent/agents/`: orchestrator and specialist agent boundaries.
- `src/riskagent/skills/`: reusable agent skills and structured extraction prompts.
- `src/riskagent/prediction/`: rule scorecard and future ML model service boundaries.
- `src/riskagent/backtesting/`: evaluation and historical risk grouping boundaries.
- `src/riskagent/reporting/`: Markdown, HTML, and future PDF report generation boundaries.
- `src/riskagent/api/`: FastAPI route and application entry boundaries.
- `src/riskagent/common/`: shared configuration, logging, exceptions, and stable types.

## Current Status

No runtime features are implemented. Add real behavior only when the corresponding module
contract and verification plan are clear.
