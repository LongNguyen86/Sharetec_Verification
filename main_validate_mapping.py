import sys
import argparse
from pathlib import Path
from datetime import datetime
from config import settings
from validators.mapping_validator import MappingValidator

sys.stdout.reconfigure(encoding='utf-8')

def run_preflight_check(specified_file: str = None):
    print("=" * 60)
    print("🛡️ TRIGGERING MAPPING PREFLIGHT VALIDATOR (ACTION #6)")
    print("=" * 60)

    ingest_dir = Path(settings.LOCAL_INGEST_DIR)

    # Get list of Mapping files to scan
    if specified_file:
        target_files = [Path(specified_file)]
    else:
        target_files = [f for f in ingest_dir.glob("*.xlsx") if "mapping" in f.name.lower()]

    if not target_files:
        print(f"❌ No Excel Mapping file found in ingest directory: {ingest_dir}")
        sys.exit(1)

    print(f"📂 Detected {len(target_files)} Mapping files to validate in ingest directory.")
    print("-" * 60)

    all_files_valid = True
    report_data = [] # Store info of all files (both Valid and Erroneous)
    total_global_errors = 0

    for file_path in target_files:
        try:
            validator = MappingValidator(str(file_path))
            is_valid, errors_by_sheet = validator.validate()

            file_errors_count = sum(len(err_list) for err_list in errors_by_sheet.values())
            total_global_errors += file_errors_count

            if not is_valid:
                all_files_valid = False
                print(f"  ❌ [{file_path.name}] ➔ Contains {file_errors_count} errors across {len(errors_by_sheet)} Sheets!")
            else:
                print(f"  ✅ [{file_path.name}] ➔ 100% VALID")

            report_data.append({
                "file_name": file_path.name,
                "is_valid": is_valid,
                "errors_count": file_errors_count,
                "errors_by_sheet": errors_by_sheet
            })

        except Exception as e:
            all_files_valid = False
            print(f"  ❌ [{file_path.name}] ➔ Error reading file: {e}")
            report_data.append({
                "file_name": file_path.name,
                "is_valid": False,
                "errors_count": 1,
                "errors_by_sheet": {"System": [f"❌ System error reading file: {e}"]}
            })

    print("-" * 60)

    # Write aggregate Preflight Report for QA
    report_dir = settings.BASE_DIR / "workspace" / "qa_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_file = report_dir / f"Mapping_Preflight_Report_{timestamp}.txt"

    with open(report_file, "w", encoding="utf-8") as f:
        f.write("=" * 80 + "\n")
        f.write(f" MAPPING FILE VALIDATION REPORT (PREFLIGHT REPORT)\n")
        f.write(f" Scan Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f" Total files scanned: {len(target_files)} file\n")
        f.write(f" Total errors detected: {total_global_errors} errors\n")
        f.write("=" * 80 + "\n\n")

        # 1. OVERALL SUMMARY OF ALL FILES
        f.write("📊 OVERALL STATUS OF FILES:\n")
        f.write("-" * 80 + "\n")
        for item in report_data:
            status_str = "✅ 100% VALID" if item["is_valid"] else f"❌ HAS ERRORS ({item['errors_count']} errors)"
            f.write(f"  • [{item['file_name']}] ➔ {status_str}\n")
        f.write("=" * 80 + "\n\n")

        # 2. DETAILS BY FILE
        f.write("🔍 DETAILS BY FILE MAPPING:\n\n")
        for item in report_data:
            f.write(f"📄 FILE MAPPING: [{item['file_name']}]\n")
            f.write("-" * 80 + "\n")
            
            if item["is_valid"]:
                f.write("  ✅ File is perfectly valid! No structural errors detected.\n\n")
            else:
                for sheet_name, err_list in item["errors_by_sheet"].items():
                    f.write(f"  📑 Sheet: [{sheet_name}] ({len(err_list)} errors)\n")
                    f.write("  " + "-" * 76 + "\n")
                    for err in err_list:
                        f.write(f"    ❌ {err}\n")
                    f.write("\n")
            f.write("\n")

    # Provide conclusion and return Exit Status Code for n8n
    if all_files_valid:
        print("✅ PREFLIGHT SUCCESS: All Mapping files are valid! Ready for Ingest Engine.")
        print(f"👉 Aggregate report saved at: {report_file}")
        print("=" * 60)
        sys.exit(0)
    else:
        print(f"❌ PREFLIGHT FAILED: Detected a total of {total_global_errors} errors across Mapping files.")
        print(f"👉 Detailed aggregate report has been exported to file:\n   {report_file}")
        print("=" * 60)
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Preflight Validate Mapping Files")
    parser.add_argument("--file", required=False, help="Specific mapping file path (if checking individually)")
    args = parser.parse_args()

    run_preflight_check(args.file)