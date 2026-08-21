import shutil
from pathlib import Path
from fastapi import APIRouter, HTTPException

from src.bsdc_engine.models.inputs import FetchInputRequest
from src.bsdc_engine.io.sharepoint import SharePointClient
from src.bsdc_engine.workspace import RunWorkspace
from src.bsdc_engine.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["Ingestion"])


def _organize_file(file_path: Path, ws: RunWorkspace) -> Path:
    """Route file to correct target directory based on filename keywords."""
    name_lower = file_path.name.lower()
    if "mapping" in name_lower:
        target_dir = ws.mapping_dir
    elif "matrix" in name_lower:
        target_dir = ws.matrix_dir
    else:
        target_dir = ws.raw_dir

    target_dir.mkdir(parents=True, exist_ok=True)
    dest_path = target_dir / file_path.name

    if file_path.resolve() != dest_path.resolve():
        shutil.move(str(file_path), str(dest_path))
        logger.info(f"Reorganized [{file_path.name}] -> {target_dir.name}")
        return dest_path
    return file_path


@router.post("/fetch-input-files")
def fetch_input_files(payload: FetchInputRequest):
    try:
        ws = RunWorkspace(run_id=getattr(payload, "run_id", None))
        client = SharePointClient()

        downloaded = []

        # 1. Fetch Mapping path
        if payload.mapping_path:
            m_files = client.fetch_paths([payload.mapping_path], output_dir=ws.mapping_dir)
            downloaded.extend(m_files)

        # 2. Fetch Matrix path
        if payload.matrix_path:
            mat_files = client.fetch_paths([payload.matrix_path], output_dir=ws.matrix_dir)
            downloaded.extend(mat_files)

        # 3. Fetch Raw Data path
        if payload.raw_data_path:
            r_files = client.fetch_paths([payload.raw_data_path], output_dir=ws.raw_dir)
            downloaded.extend(r_files)

        if not downloaded:
            raise HTTPException(status_code=400, detail="No files downloaded from provided SharePoint paths.")

        # Re-organize files strictly by filename rules
        final_files = []
        for f_path in downloaded:
            if f_path.exists():
                organized = _organize_file(f_path, ws)
                final_files.append(organized)

        return {
            "status": "success",
            "run_id": ws.run_id,
            "downloaded_files_count": len(final_files),
            "files": [str(p) for p in final_files],
        }
    except Exception as e:
        logger.error(f"Fetch Input Files failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))