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

    def convert_all_in_dir(self, input_dir: Path) -> list[Path]:
        input_path = Path(input_dir)
        if not input_path.exists():
            logger.warning(f"No Excel data files found in directory: {input_path}")
            return []

        excel_files = []
        for file in input_path.glob("*"):
            if file.is_file() and not file.name.startswith("~$"):
                if "mapping" in file.name.lower():
                    logger.info(f"Skipped Mapping file from CSV conversion: {file.name}")
                    continue

                if file.suffix.lower() in [".xlsx", ".xls"]:
                    excel_files.append(file)

        logger.info(f"Found {len(excel_files)} Excel data files. Starting conversion...")
        all_csvs = []
        for file in excel_files:
            csvs = self.convert_single_file(file)
            all_csvs.extend(csvs)

        return all_csvs