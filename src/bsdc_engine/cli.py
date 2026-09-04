import argparse
import sys
from pathlib import Path

from src.bsdc_engine.workspace import RunWorkspace
from src.bsdc_engine.verify.key_matcher import KeyMatcher, detect_key_columns


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

    # Command: verify
    parser_verify = subparsers.add_parser("verify", help="Run Perform Verification comparing Expected vs Sharetec Actual data")
    parser_verify.add_argument("--run-id", required=False, help="Isolated Run ID")
    parser_verify.add_argument("--section-name", required=False, default=None, help="Optional Section CSV filename without extension")
    parser_verify.add_argument("--key-cols", nargs="*", default=None, help="Optional Business key columns for matching")
    parser_verify.add_argument("--compare-cols", nargs="*", default=None, help="Optional specific compare columns")
    parser_verify.add_argument("--numeric-cols", nargs="*", default=[], help="Numeric columns for control totals check")

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
        parse_all_mapping_sheets(raw_dir=ws.mapping_dir, cu_id=getattr(args, "cu_id", None))
        print(f"✅ Rule parsing completed. Extracted rules from {ws.mapping_dir} into SQLite database.")

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
        from src.bsdc_engine.generate.builders import TransformationBuilder
        from src.bsdc_engine.config import settings

        out_dir = getattr(ws, "transformed_dir", ws.reconciliation_dir)
        db_file = getattr(ws, "db_path", None) or settings.db_path

        builder = TransformationBuilder(
            raw_data_dir=ws.csv_dir,
            output_dir=out_dir,
            db_path=db_file
        )
        results = builder.generate_all(cu_id=getattr(args, "cu_id", None))
        print(f"✅ Transformation completed. Generated {len(results)} tables into {out_dir}.")

    elif args.command == "verify":
        import polars as pl
        from src.bsdc_engine.verify.comparator import DataComparator
        from src.bsdc_engine.verify.formats import FormatValidator
        from src.bsdc_engine.verify.aggregates import AggregateChecker
        from src.bsdc_engine.verify.reporter import VerificationReporter

        run_root = ws.raw_dir.parent.parent
        exp_dir = getattr(ws, "reconciliation_dir", None) or (run_root / "out" / "reconciliation")
        
        # Centralized Sharetec Actual Directory in workspace
        act_dir = Path("workspace/Actual_Sharetec")
        act_dir.mkdir(parents=True, exist_ok=True)

        if args.section_name:
            target_files = [exp_dir / f"{args.section_name}.csv"]
        else:
            target_files = list(exp_dir.glob("Expected_*.csv"))

        if not target_files:
            print(f"❌ No Expected CSV files found in: {exp_dir}")
            sys.exit(1)

        all_section_results = []

        for exp_file in target_files:
            sec_stem = exp_file.stem
            act_file = act_dir / f"{sec_stem}.csv"

            if not act_file.exists():
                print(f"⚠️ Skipping [{sec_stem}]: File missing in central directory {act_dir}")
                continue

            df_exp = pl.read_csv(exp_file, infer_schema_length=0)
            df_act = pl.read_csv(act_file, infer_schema_length=0)

            key_columns = args.key_cols if args.key_cols else detect_key_columns(df_exp.columns, sec_stem)
            compare_columns = args.compare_cols if args.compare_cols else df_exp.columns

            exp_map = KeyMatcher.build_record_map(df_exp, key_columns, compare_columns)
            act_map = KeyMatcher.build_record_map(df_act, key_columns, compare_columns)

            mismatches = DataComparator.compare_maps(exp_map, act_map, compare_columns)
            fmt_issues = FormatValidator.validate_field_formats(act_map)
            agg_issues = AggregateChecker.check_control_totals(exp_map, act_map, args.numeric_cols)

            all_section_results.append({
                "section_name": sec_stem,
                "expected_rows": len(exp_map),
                "actual_rows": len(act_map),
                "mismatches": mismatches,
                "fmt_issues": fmt_issues,
                "agg_issues": agg_issues
            })

            print(f"✅ Verified [{sec_stem}]: Key={key_columns} | Discrepancies={len(mismatches)}")

        VerificationReporter.generate_combined_report(all_section_results, output_dir=Path("test-output"))


if __name__ == "__main__":
    main()