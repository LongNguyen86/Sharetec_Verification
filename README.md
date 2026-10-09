# Sharetec Verification Engine (`sharetec-verification`)

An automated ETL, Data Transformation, and QA Verification Engine designed for Credit Union (CU) data migration into the Sharetec core processing system.

---

## 🚀 Core Features

- **SharePoint Data Ingestion:** Automatically downloads mapping, matrix, raw data, and Sharetec actual files directly from SharePoint based on Credit Union IDs (`cu_id`) or custom SharePoint relative paths.
- **Mapping Validation:** Pre-validates structure and integrity of Excel mapping files prior to parsing.
- **Excel Converter:** Converts raw Excel data files (.xlsx/.xls) into CSV format for high-speed transformation processing.
- **Rule Parser & DSL Engine:** Parses complex Excel Mapping sheets into structured Domain-Specific Language (DSL) models and stores rules in a local SQLite database (`rules.db`).
- **AI Rule Drafting:** Optionally drafts complex transformation rules using Gemini AI. (Pending)
- **QA Verification & Decision Engine:** Generates Excel QA verification reports for rule review and applies QA decisions (`APPROVE`, `EDIT`, `REJECT`) back into the database.
- **Polars Transformation Builder:** High-performance data execution powered by Polars with sheet-level output unification, dynamic matrix lookup, and original input row order preservation.
- **Verification & Worksheet Assembly:** Compares generated output against actual Shar

---

## 🛠️ Prerequisites & Installation

### Requirements
- **Python:** `>= 3.10`

### 1. Environment Setup
Clone the repository and set up a virtual environment:

```bash
git clone <REPOSITORY_URL>
cd Sharetec_Verification

#Create a virtual environment
python -m venv venv

#Activate the environment
Ctrl + Shift + P
Python: Select Interpreter

# Activate Virtual Environment:
# On Windows (PowerShell):
.\venv\Scripts\activate
# On Linux / macOS:
source venv/bin/activate

# Install Dependencies
pip install -e .

### RUN TEST EACH FEATURE BY CLI

#1 Initialize Database Schema
python -m src.bsdc_engine.cli init-db

#2. Fetch Data from SharePoint (Ingestion)
python -m src.bsdc_engine.cli ingest --cu-id <Name of CU> (ex: EVIZI)

#3. Convert Raw Excel Data to CSV
python -m src.bsdc_engine.cli convert --run-id <id> (ex: run_EVIZI_20261009_103753)

#4. Validate the mapping file
python -m src.bsdc_engine.cli validate-mapping --run-id <id> 
Generated output location: workspace/runs/<RUN_ID>/output/qa-reports/

#5. Parse Mapping Rules
python -m src.bsdc_engine.cli parse-rules --run-id <id> 

#6. QA Review Process
python -m src.bsdc_engine.cli export-qa --run-id <id> 
Generated output location: workspace/runs/<RUN_ID>/output/qa-reports/

#7. Apply Reviewed QA Decisions to DB:
python -m src.bsdc_engine.cli apply-qa --report-file <report file name>

#8. Execute Data Transformation Engine
python -m src.bsdc_engine.cli generate --run-id <id> 
Generated output location: workspace/runs/<RUN_ID>/output/reconciliation/

#9. Verify Data & Assemble HTML Worksheets
python -m src.bsdc_engine.cli verify --run-id <id> 
python -m src.bsdc_engine.cli assemble-worksheet --run-id <id> 
Generated output location: workspace/runs/<RUN_ID>/test-output


### RUN TEST FLOW VIA n8n

#1. Start n8n
Open Terminal/Powershell : npn n8n

#2. Start FastAPI server
$env:PYTHONPATH="." ; python -m uvicorn src.bsdc_engine.api.app:app --reload

#3. Open Browser
http://localhost:5678/home/workflows


Sharetec_Verification/
├── .gitea/                    # Gitea CI/CD workflows
├── migrations/                # Database migration SQL scripts (001_init_rule_store.sql)
├── n8n_workflows/             # Automation workflow definitions
├── src/
│   └── bsdc_engine/
│       ├── api/               # FastAPI route handlers for n8n integration
│       ├── expr/              # Expression evaluation modules
│       ├── generate/          # Polars execution engines & transformation builders
│       ├── io/                # SharePoint client, Excel converters & file readers
│       ├── metrics/           # Metric tracking and execution logging
│       ├── models/            # Pydantic DSL and API payload schemas
│       ├── report/            # Rule verification Excel report generators
│       ├── rulegen/           # AI-assisted rule drafting (Gemini integration)
│       ├── rules/             # Rule parser, decision applier & SQLite store
│       ├── validate/          # Schema and mapping validation utilities
│       ├── verify/            # Comparators, format checkers & reporter
│       ├── worksheet/         # HTML verification worksheet assembly modules
│       ├── cli.py             # Main CLI Entrypoint
│       ├── config.py          # Environment settings
│       ├── workspace.py       # Isolated run workspace management
│       └── logging.py         # Project logging configuration
├── pyproject.toml             # Project dependencies and package configuration
├── docker-compose.yml         # Container setup
├── Dockerfile                 # Container build definition
└── workspace/                 # Local working directory (Ignored by Git)
    └── runs/                  # Execution run isolation workspaces (<run_id>)