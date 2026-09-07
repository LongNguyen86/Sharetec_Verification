import shutil
from pathlib import Path
from fastapi import APIRouter, HTTPException

from src.bsdc_engine.models.inputs import FetchInputRequest
from src.bsdc_engine.io.sharepoint import SharePointClient
from src.bsdc_engine.errors import SharePointAuthError
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
        # Extract run_id explicitly from payload or default to None for auto-generation
        requested_run_id = payload.run_id if hasattr(payload, "run_id") and payload.run_id else None
        ws = RunWorkspace(run_id=requested_run_id)

        client = SharePointClient()

        # If paths are not explicitly provided, resolve them dynamically using cu_id
        mapping_p = payload.mapping_path
        matrix_p = payload.matrix_path
        raw_p = getattr(payload, "raw_data_path", None)

        if not mapping_p or not matrix_p:
            auto_paths = client.resolve_cu_paths(payload.cu_id)
            mapping_p = mapping_p or auto_paths.get("mapping_path")
            matrix_p = matrix_p or auto_paths.get("matrix_path")
            raw_p = raw_p or auto_paths.get("raw_data_path")

        downloaded = []
        if mapping_p:
            downloaded.extend(client.fetch_paths([mapping_p], output_dir=ws.mapping_dir))
        if matrix_p:
            downloaded.extend(client.fetch_paths([matrix_p], output_dir=ws.matrix_dir))
        if raw_p:
            downloaded.extend(client.fetch_paths([raw_p], output_dir=ws.raw_dir))

        if not downloaded:
            raise HTTPException(
                status_code=400, 
                detail=f"No files downloaded for CU [{payload.cu_id}]. Please check SharePoint folder existence and permissions."
            )

        final_files = [_organize_file(f, ws) for f in downloaded if f.exists()]

        return {
            "status": "success",
            "run_id": ws.run_id,
            "downloaded_files_count": len(final_files),
            "files": [str(p) for p in final_files],
        }
    except SharePointAuthError as e:
        logger.error(f"SharePoint Auth Failure: {e}")
        raise HTTPException(
            status_code=401,
            detail="SharePoint Session Expired or Authentication Required. The expired session was cleared. Please re-run the step to complete 2FA on your phone."
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Fetch Input Files failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))