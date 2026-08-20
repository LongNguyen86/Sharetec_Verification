from pathlib import Path
import polars as pl
from src.bsdc_engine.logging import get_logger

logger = get_logger(__name__)


def load_raw_tables(raw_data_dir: Path) -> dict[str, pl.DataFrame]:
    """Load all CSV files from raw_data folder into RAM as Polars DataFrames."""
    raw_data_dir = Path(raw_data_dir)
    if not raw_data_dir.exists():
        logger.warning(f"Raw data directory does not exist: {raw_data_dir}")
        return {}

    tables: dict[str, pl.DataFrame] = {}
    for file_path in raw_data_dir.glob("*.csv"):
        table_name = file_path.stem.upper().replace(" ", "_")
        try:
            try:
                df = pl.read_csv(file_path, infer_schema_length=0, ignore_errors=True, encoding="utf8")
            except Exception:
                df = pl.read_csv(file_path, infer_schema_length=0, ignore_errors=True, encoding="latin1")
            tables[table_name] = df
            logger.info(f"Loaded Raw Table [{table_name}]: {df.shape[0]} rows, {df.shape[1]} cols")
        except Exception as e:
            logger.error(f"Error reading file {file_path.name}: {e}")
    return tables