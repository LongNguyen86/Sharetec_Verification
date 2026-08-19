import sys
import argparse
from config import settings
from io_engine.excel_converter import ExcelConverter

# Force UTF-8 encoding for Windows Terminal
sys.stdout.reconfigure(encoding='utf-8')

def run_conversion(file_path: str = None):
    print("=" * 60)
    print("⚡ TRIGGERING EXCEL TO CSV CONVERTER ENGINE (ACTION #4)")
    print("=" * 60)

    converter = ExcelConverter()

    if file_path:
        csv_files = converter.convert_xlsx_to_csv(file_path)
    else:
        # Default: Convert all Excel files located in workspace/ingest
        csv_files = converter.convert_all_in_dir()

    print("-" * 60)
    print(f"🎉 SUMMARY: Generated {len(csv_files)} CSV files at {settings.LOCAL_CSV_DIR}")
    return csv_files

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert Excel files to CSV")
    parser.add_argument("--file", required=False, help="Specific Excel file path (optional)")
    args = parser.parse_args()

    run_conversion(args.file)