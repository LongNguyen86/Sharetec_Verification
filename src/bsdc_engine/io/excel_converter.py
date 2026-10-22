from pathlib import Path
import pandas as pd
from src.bsdc_engine.logging import get_logger

logger = get_logger(__name__)


class ExcelConverter:
    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def convert_single_file(self, file_path: Path) -> list[Path]:
        file_path = Path(file_path)
        if not file_path.exists():
            logger.error(f"File does not exist: {file_path}")
            return []

        logger.info(f"Reading Excel file: {file_path.name}...")
        csv_files = []
        try:
            xl = pd.ExcelFile(file_path)
        except Exception as e:
            logger.warning(f"SKIPPED: Cannot read file [{file_path.name}]: {e}")
            return []

        for sheet in xl.sheet_names:
            try:
                df = pd.read_excel(file_path, sheet_name=sheet)
                if df.empty or df.dropna(how="all").empty:
                    logger.info(f"Skipped empty Sheet: [{sheet}]")
                    continue

                clean_sheet = (
                    sheet.replace(" ", "_")
                    .replace("(", "")
                    .replace(")", "")
                    .replace("-", "_")
                )
                csv_filename = self.output_dir / f"{file_path.stem}_{clean_sheet}.csv"
                df.to_csv(csv_filename, index=False, encoding="utf-8-sig")
                csv_files.append(csv_filename)
                logger.info(f"Successfully converted Sheet [{sheet}] -> {csv_filename.name}")
            except Exception as e:
                logger.error(f"Error converting Sheet [{sheet}]: {e}")

        return csv_files

    def convert_d_file(self, file_path: Path) -> Path | None:
        """Convert a Progress OpenEdge .d data dump file into standard CSV."""
        file_path = Path(file_path)
        if not file_path.exists():
            logger.error(f"File does not exist: {file_path}")
            return None

        logger.info(f"Reading Progress .d file: {file_path.name}...")
        try:
            csv_filename = self.output_dir / f"{file_path.stem}.csv"
            # Read raw .d data dump file via robust Pandas C-engine
            df = pd.read_csv(
                file_path,
                dtype=str,
                encoding="utf-8",
                encoding_errors="replace",
                keep_default_na=False,
            )
            df.to_csv(csv_filename, index=False, encoding="utf-8-sig")
            logger.info(f"Successfully converted .d file -> {csv_filename.name}")
            return csv_filename
        except Exception as e:
            logger.error(f"Error converting .d file [{file_path.name}]: {e}")
            return None

    def convert_all_in_dir(self, input_dir: Path) -> list[Path]:
        input_path = Path(input_dir)
        if not input_path.exists():
            logger.warning(f"No data files found in directory: {input_path}")
            return []

        all_csvs = []
        excel_extensions = [".xlsx", ".xls"]

        for file in input_path.glob("*"):
            if file.is_file() and not file.name.startswith("~$"):
                if "mapping" in file.name.lower():
                    logger.info(f"Skipped Mapping file from CSV conversion: {file.name}")
                    continue

                file_ext = file.suffix.lower()

                # Process Excel spreadsheet workbooks
                if file_ext in excel_extensions:
                    csvs = self.convert_single_file(file)
                    all_csvs.extend(csvs)

                # Process Progress OpenEdge data dump files
                elif file_ext == ".d":
                    converted_csv = self.convert_d_file(file)
                    if converted_csv:
                        all_csvs.append(converted_csv)

        logger.info(f"Completed conversion process. Generated {len(all_csvs)} CSV files in total.")
        return all_csvs