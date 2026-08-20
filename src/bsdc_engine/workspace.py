import uuid
from pathlib import Path
from src.bsdc_engine.config import settings


class RunWorkspace:

    def __init__(self, run_id: str | None = None):
        self.run_id = run_id or f"run_{uuid.uuid4().hex[:8]}"

        # Safely resolve workspace directory with fallback
        workspace_base = getattr(settings, "workspace_dir", Path("workspace"))
        self.base_dir = Path(workspace_base) / "runs" / self.run_id

        # Input & Raw data
        self.raw_dir = self.base_dir / "in" / "raw"
        self.mapping_dir = self.base_dir / "in" / "mapping"

        # Working directory for CSVs and Matrix
        self.work_dir = self.base_dir / "work"
        self.csv_dir = self.work_dir / "csv"
        self.matrix_dir = self.work_dir / "matrix"

        # Outputs
        self.output_dir = self.base_dir / "out"
        self.qa_reports_dir = self.output_dir / "qa_reports"
        self.reconciliation_dir = self.output_dir / "reconciliation"

        self._ensure_dirs()

    def _ensure_dirs(self):
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.mapping_dir.mkdir(parents=True, exist_ok=True)
        self.csv_dir.mkdir(parents=True, exist_ok=True)
        self.matrix_dir.mkdir(parents=True, exist_ok=True)
        self.qa_reports_dir.mkdir(parents=True, exist_ok=True)
        self.reconciliation_dir.mkdir(parents=True, exist_ok=True)