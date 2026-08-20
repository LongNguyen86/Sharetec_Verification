from fastapi import APIRouter, HTTPException
from src.bsdc_engine.models.inputs import FetchInputRequest
from src.bsdc_engine.io.sharepoint import SharePointClient
from src.bsdc_engine.workspace import RunWorkspace
from src.bsdc_engine.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["Ingestion"])


@router.post("/fetch-input-files")
def fetch_input_files(payload: FetchInputRequest):
    try:
        ws = RunWorkspace()
        client = SharePointClient()

        paths = [p for p in [payload.mapping_path, payload.raw_data_path, payload.matrix_path] if p]
        if not paths:
            raise HTTPException(status_code=400, detail="No SharePoint paths provided in request.")

        downloaded = client.fetch_paths(paths, output_dir=ws.raw_dir)
        
        return {
            "status": "success",
            "run_id": ws.run_id,
            "downloaded_files_count": len(downloaded),
            "files": [str(p) for p in downloaded],
        }
    except Exception as e:
        logger.error(f"Fetch Input Files failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))