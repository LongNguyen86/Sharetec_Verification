import argparse
import sys
from pathlib import Path

from src.bsdc_engine.workspace import RunWorkspace


def get_latest_run_id() -> str | None:
    runs_dir = Path("workspace/runs")
    if not runs_dir.exists():
        return None

    valid_runs = [
        d for d in runs_dir.iterdir()
        if d.is_dir() and (d / "in" / "raw").exists() and any((d / "in" / "raw").iterdir())
    ]

    if valid_runs:
        return max(valid_runs, key=lambda x: x.stat().st_mtime).name

    run_folders = [d for d in runs_dir.iterdir() if d.is_dir()]
    return max(run_folders, key=lambda x: x.stat().st_mtime).name if run_folders else None


def main():
    parser = argparse.ArgumentParser(description="BSDC Engine Unified CLI Tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Command: init-db
    parser_init = subparsers.add_parser("init-db", help="Reset and re-initialize SQLite Database schema")

    # Command: ingest
    parser_ingest = subparsers.add_parser("ingest", help="Fetch files from SharePoint")
    parser_ingest.add_argument("--paths", nargs="+", required=True, help="SharePoint server relative paths")
    parser_ingest.add_argument("--run-id", required=False, help="Isolated Run ID")

    # Command: convert
    parser_convert = subparsers.add_parser("convert", help="Convert Excel files to CSV")
    parser_convert.add_argument("--run-id", required=False, help="Isolated Run ID")

    # Command: parse-rules
    parser_rules = subparsers.add_parser("parse-rules", help="Parse Excel Mapping rules into SQLite Database")
    parser_rules.add_argument("--run-id", required=False, help="Isolated Run ID")
    parser_rules.add_argument("--cu-id", required=False, help="Optional Credit Union ID")

    # Command: ai-parse
    parser_ai = subparsers.add_parser("ai-parse", help="Draft complex mapping rules using Gemini AI")
    parser_ai.add_argument("--run-id", required=False, help="Isolated Run ID")

    # Command: export-qa
    parser_export = subparsers.add_parser("export-qa", help="Export Rule Verification Excel report for QA Review")
    parser_export.add_argument("--run-id", required=False, help="Isolated Run ID")
    parser_export.add_argument("--cu-id", required=False, help="Optional Credit Union ID")

    # Command: apply-qa
    parser_apply = subparsers.add_parser("apply-qa", help="Apply QA review decisions (APPROVE/EDIT/REJECT) to DB")
    parser_apply.add_argument("--run-id", required=False, help="Isolated Run ID")
    parser_apply.add_argument("--report-file", required=True, help="Filename of reviewed report")

    # Command: generate
    parser_generate = subparsers.add_parser("generate", help="Run Transformation Engine")
    parser_generate.add_argument("--run-id", required=False, help="Isolated Run ID")
    parser_generate.add_argument("--cu-id", required=False, help="Credit Union ID")

    args = parser.parse_args()

    if args.command == "init-db":
        from src.bsdc_engine.config import settings
        from src.bsdc_engine.rules.store import RuleStore

        db_path = settings.db_path
        if db_path.exists():
            try:
                db_path.unlink()
            except PermissionError:
                print(f"❌ Cannot reset database: File is locked by another process.")
                print("💡 Please close DB Browser for SQLite or stop the Uvicorn server, then re-run.")
                sys.exit(1)

        store = RuleStore()
        store.init_schema()
        print(f"✅ Database successfully reset and re-initialized at: {store.db_path}")
        return

    target_run_id = getattr(args, "run_id", None) or get_latest_run_id()
    ws = RunWorkspace(run_id=target_run_id)

    if args.command == "ingest":
        from src.bsdc_engine.io.sharepoint import SharePointClient
        client = SharePointClient()
        downloaded = client.fetch_paths(args.paths, output_dir=ws.raw_dir)
        print(f"✅ Ingest completed. Downloaded {len(downloaded)} files to {ws.raw_dir}")

    elif args.command == "convert":
        from src.bsdc_engine.io.excel_converter import ExcelConverter
        converter = ExcelConverter(output_dir=ws.csv_dir)
        csv_files = converter.convert_all_in_dir(input_dir=ws.raw_dir)
        print(f"✅ Conversion completed. Generated {len(csv_files)} CSV files in {ws.csv_dir}")

    elif args.command == "parse-rules":
        from src.bsdc_engine.rules.parser import parse_all_mapping_sheets
        parse_all_mapping_sheets(raw_dir=ws.raw_dir, cu_id=getattr(args, "cu_id", None))
        print(f"✅ Rule parsing completed. Extracted rules from {ws.raw_dir} into SQLite database.")

    elif args.command == "ai-parse":
        from src.bsdc_engine.rulegen.drafter import RuleDrafter
        drafter = RuleDrafter()
        count = drafter.draft_pending_rules()
        print(f"✅ AI Drafting completed. Processed {count} rules via Gemini.")

    elif args.command == "export-qa":
        from src.bsdc_engine.report.rule_verification import export_rule_verification_report
        export_rule_verification_report(output_dir=ws.qa_reports_dir, cu_id=getattr(args, "cu_id", None))

    elif args.command == "apply-qa":
        from src.bsdc_engine.rules.decisions import apply_qa_decisions
        report_path = ws.qa_reports_dir / args.report_file
        apply_qa_decisions(reviewed_report_path=report_path)
        
    elif args.command == "generate":
        from src.bsdc_engine.generate.builder import TransformationBuilder
        builder = TransformationBuilder(raw_data_dir=ws.csv_dir, output_dir=ws.reconciliation_dir)
        results = builder.generate_all(cu_id=getattr(args, "cu_id", None))
        print(f"✅ Transformation completed. Generated {len(results)} tables.")


if __name__ == "__main__":
    main()