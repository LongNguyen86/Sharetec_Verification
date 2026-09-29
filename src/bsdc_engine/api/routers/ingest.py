import shutil
import re
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
    if file_path.parent.resolve() in (
        ws.mapping_dir.resolve(),
        ws.matrix_dir.resolve(),
        ws.actual_dir.resolve(),
        ws.raw_dir.resolve(),
    ):
        return file_path

    name_lower = file_path.name.lower()
    if "mapping" in name_lower:
        target_dir = ws.mapping_dir
    elif "matrix" in name_lower:
        target_dir = ws.matrix_dir
    elif "actual" in name_lower or "sharetec" in name_lower:
        target_dir = ws.actual_dir
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
        client = SharePointClient()

        mapping_p = payload.mapping_path
        matrix_p = payload.matrix_path
        actual_p = payload.actual_path
        raw_p = payload.raw_data_path

        if not mapping_p or not matrix_p:
            auto_paths = client.resolve_cu_paths(payload.cu_id)
            
            if not auto_paths or not any(auto_paths.values()):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid CU Name [{payload.cu_id}]. No matching CU directory found on SharePoint."
                )

            mapping_p = mapping_p or auto_paths.get("mapping_path")
            matrix_p = matrix_p or auto_paths.get("matrix_path")
            actual_p = actual_p or auto_paths.get("actual_path")
            raw_p = raw_p or auto_paths.get("raw_data_path")

        requested_run_id = payload.run_id if hasattr(payload, "run_id") and payload.run_id else None
        ws = RunWorkspace(run_id=requested_run_id)

        # DOWNLOAD FILES PARALLEL
        downloaded = []
        if mapping_p:
            downloaded.extend(client.fetch_paths([mapping_p], output_dir=ws.mapping_dir, cu_id=payload.cu_id, folder_type="mapping"))
        if matrix_p:
            downloaded.extend(client.fetch_paths([matrix_p], output_dir=ws.matrix_dir, cu_id=payload.cu_id, folder_type="matrix"))
        if actual_p:
            downloaded.extend(client.fetch_paths([actual_p], output_dir=ws.actual_dir, cu_id=payload.cu_id, folder_type="actual"))
        if raw_p:
            downloaded.extend(client.fetch_paths([raw_p], output_dir=ws.raw_dir, cu_id=payload.cu_id, folder_type="raw"))

        if not downloaded:
            if ws.base_dir.exists():
                shutil.rmtree(ws.base_dir, ignore_errors=True)
            raise HTTPException(
                status_code=400, 
                detail=f"No files downloaded for CU [{payload.cu_id}]. Please check SharePoint folder existence."
            )

        final_files = [_organize_file(f, ws) for f in downloaded if f.exists()]

        # CLEANUP NON-MATCHING MAPPING FILES
        if payload.cu_id and ws.mapping_dir.exists():
            clean_cu = re.sub(r'[^a-z0-9]', '', payload.cu_id.lower())
            for f in list(ws.mapping_dir.glob("*.xlsx")):
                if "mapping" in f.name.lower():
                    clean_fname = re.sub(r'[^a-z0-9]', '', f.name.lower())
                    if clean_cu not in clean_fname:
                        logger.info(f"Removing non-matching mapping file: {f.name} for CU [{payload.cu_id}]")
                        f.unlink(missing_ok=True)

        final_files = [f for f in final_files if f.exists()]

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
            detail="SharePoint Session Expired or Authentication Required. Please re-run 2FA authentication."
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Fetch Input Files failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))