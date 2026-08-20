import os
from pathlib import Path
import pandas as pd
from legacy.config import settings


class ExcelConverter:
    def __init__(self, output_dir=None):
        # Ép kiểu Path chắc chắn 100% kể cả khi settings truyền vào chuỗi string
        target_dir = output_dir if output_dir else settings.LOCAL_CSV_DIR
        self.output_dir = Path(target_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def convert_single_file(self, file_path: Path):
        file_path = Path(file_path)
        if not file_path.exists():
            print(f"❌ File does not exist: {file_path}")
            return []

        print(f"📖 Reading Excel file: {file_path.name}...")
        csv_files = []
        try:
            xl = pd.ExcelFile(file_path)
        except Exception as e:
            print(f"⚠️ SKIPPED: Cannot read file [{file_path.name}]: {e}")
            return []

        for sheet in xl.sheet_names:
            try:
                df = pd.read_excel(file_path, sheet_name=sheet)
                if df.empty or df.dropna(how="all").empty:
                    print(f"   ⏩ Skipped empty Sheet: [{sheet}]")
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
                print(f"   ✅ Successfully converted Sheet [{sheet}] -> {csv_filename.name}")
            except Exception as e:
                print(f"   ❌ Error converting Sheet [{sheet}]: {e}")

        return csv_files

    def convert_all_in_dir(self, input_dir=None):
        target_input = input_dir if input_dir else settings.LOCAL_INGEST_DIR
        input_path = Path(target_input)
        if not input_path.exists():
            print(f"⚠️ No Excel data files found to convert in directory: {input_path}")
            return []

        excel_files = []
        for file in input_path.glob("*"):
            if file.is_file() and not file.name.startswith("~$"):
                if "mapping" in file.name.lower():
                    print(f"⏩ Skipped Mapping file (no CSV conversion): {file.name}")
                    continue

                if file.suffix.lower() in [".xlsx", ".xls"]:
                    excel_files.append(file)

        print(f"🔄 Found {len(excel_files)} Excel data files. Starting conversion...")
        all_csvs = []
        for file in excel_files:
            csvs = self.convert_single_file(file)
            all_csvs.extend(csvs)

        return all_csvs