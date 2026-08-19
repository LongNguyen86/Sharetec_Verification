import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Root directory of the project
BASE_DIR = Path(__file__).parent.parent.resolve()

# SharePoint Site URL
SITE_URL = os.getenv("SHAREPOINT_SITE_URL")

USERNAME = os.getenv("SHAREPOINT_USERNAME")
PASSWORD = os.getenv("SHAREPOINT_PASSWORD")

# Directory to store ingested files downloaded from SharePoint
LOCAL_INGEST_DIR = BASE_DIR / os.getenv("LOCAL_INGEST_DIR", "workspace/ingest")

# Directory to store converted CSV files
LOCAL_CSV_DIR = BASE_DIR / os.getenv("LOCAL_CSV_DIR", "workspace/csv")

# Directory containing Excel mapping files
LOCAL_MAPPING_DIR = BASE_DIR / os.getenv("LOCAL_MAPPING_DIR", "workspace/mapping")

# Directory to store QA validation reports
LOCAL_REPORT_DIR = BASE_DIR / os.getenv("LOCAL_REPORT_DIR", "workspace/qa_reports")