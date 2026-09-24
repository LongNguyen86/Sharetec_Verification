import uuid
from pathlib import Path
from src.bsdc_engine.config import settings


class RunWorkspace:

    def __init__(self, run_id: str | None = None):
        self.run_id = run_id or f"run_{uuid.uuid4().hex[:8]}"

        # Safely resolve workspace base directory
        workspace_base = getattr(settings, "workspace_dir", Path("workspace"))
        self.base_dir = Path(workspace_base) / "runs" / self.run_id

        # 1. Input Directory Structure (in)
        self.in_dir = self.base_dir / "input"
        self.raw_dir = self.in_dir / "raw_data"
        self.mapping_dir = self.in_dir / "mapping"
        self.matrix_dir = self.in_dir / "matrix"            # Moved from work/matrix to in/matrix
        self.actual_dir = self.in_dir / "actual_sharetec"

        # 2. Output Directory Structure (out)
        self.out_dir = self.base_dir / "output"
        self.csv_dir = self.out_dir / "csv"                  # Moved from work/csv to out/csv
        self.qa_reports_dir = self.out_dir / "qa_reports"
        self.reconciliation_dir = self.out_dir / "reconciliation"

        # 3. Test Output Directory Structure (test-output at root run_id level)
        self.test_output_dir = self.base_dir / "test-output"

        self._ensure_dirs()

    def _ensure_dirs(self):
        """Ensure all required input and output directories exist on disk."""
        # Create input directories
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.mapping_dir.mkdir(parents=True, exist_ok=True)
        self.matrix_dir.mkdir(parents=True, exist_ok=True)
        self.actual_dir.mkdir(parents=True, exist_ok=True)

        # Create output directories
        self.csv_dir.mkdir(parents=True, exist_ok=True)
        self.qa_reports_dir.mkdir(parents=True, exist_ok=True)
        self.reconciliation_dir.mkdir(parents=True, exist_ok=True)

        # 3. Test Output Directory Structure (test-output at root run_id level)
        self.test_output_dir = self.base_dir / "test-output"