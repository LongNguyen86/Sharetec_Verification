import argparse
import sys
import re
import shutil
from pathlib import Path

from src.bsdc_engine.workspace import RunWorkspace
from src.bsdc_engine.verify.key_matcher import KeyMatcher, detect_key_columns


def get_latest_run_id() -> str | None:
    """Retrieve the most recent run_id directory from workspace/runs."""
    runs_dir = Path("workspace/runs")
    if not runs_dir.exists():
        return None

    valid_runs = [
        d for d in runs_dir.iterdir()
        if d.is_dir() and (d / "input" / "raw_data").exists() and any((d / "input" / "raw_data").iterdir())
    ]

    if valid_runs:
        return max(valid_runs, key=lambda x: x.stat().st_mtime).name

    run_folders = [d for d in runs_dir.iterdir() if d.is_dir()]
    return max(run_folders, key=lambda x: x.stat().st_mtime).name if run_folders else None


def _resolve_cu_id(args_cu_id: str | None, ws: RunWorkspace) -> str | None:
    """Automatically resolve Credit Union ID from CLI args, run_id pattern, or SQLite DB."""
    if args_cu_id:
        return args_cu_id

    # 1. Extract from run_id naming pattern (e.g., run_EVIZI_20261009_113627 -> EVIZI)
    match = re.search(r"run_([A-Za-z0-9]+)_\d+", ws.run_id)
    if match:
        return match.group(1)

    # 2. Fallback to querying rule_store SQLite DB
    try:
        from src.bsdc_engine.rules.store import RuleStore
        from src.bsdc_engine.config import settings

        db_file = getattr(ws, "db_path", None) or settings.db_path
        if db_file.exists():
            store = RuleStore(db_path=db_file)
            with store.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT DISTINCT cu_id FROM rule_store WHERE cu_id IS NOT NULL AND cu_id != '' LIMIT 1"
                )
                row = cursor.fetchone()
                if row:
                    return row[0]
    except Exception:
        pass

    return None


def _organize_file(file_path: Path, ws: RunWorkspace) -> Path:
    """Route downloaded file to correct target subfolder based on filename keywords."""
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
        return dest_path
    return file_path


def main():
    parser = argparse.ArgumentParser(description="BSDC Engine Unified CLI Tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Command: init-db
    parser_init = subparsers.add_parser("init-db", help="Reset and re-initialize SQLite Database schema")

    # Command: ingest
    parser_ingest = subparsers.add_parser("ingest", help="Fetch input files from SharePoint automatically or via path")
    parser_ingest.add_argument("--cu-id", required=False, help="Credit Union ID (e.g. EVIZI, MEDICOOP)")
    parser_ingest.add_argument("--sp-path", required=False, help="Optional SharePoint base directory path for CU")
    parser_ingest.add_argument("--paths", nargs="+", required=False, help="Optional explicit SharePoint server relative paths")
    parser_ingest.add_argument("--run-id", required=False, help="Isolated Run ID")

    # Command: validate-mapping
    parser_validate = subparsers.add_parser("validate-mapping", help="Validate mapping Excel files prior to parsing rules")
    parser_validate.add_argument("--run-id", required=False, help="Isolated Run ID")
    parser_validate.add_argument("--cu-id", required=False, help="Optional Credit Union ID")

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

    # Command: assemble-worksheet
    parser_worksheet = subparsers.add_parser("assemble-worksheet", help="Extract field samples (Failed-first) and assemble HTML Verification Worksheet")
    parser_worksheet.add_argument("--run-id", required=False, help="Isolated Run ID")
    parser_worksheet.add_argument("--cu-id", required=False, help="Credit Union ID")
    parser_worksheet.add_argument("--sp-template-path", required=False, help="Optional relative SharePoint path to HTML template")

    args = parser.parse_args()

    if args.command == "init-db":
        from src.bsdc_engine.config import settings
        from src.bsdc_engine.rules.store import RuleStore

        db_path = settings.db_path
        if db_path.exists():
            try:
                db_path.unlink()
            except PermissionError:
                print("❌ Cannot reset database: File is locked by another process.")
                print("💡 Please close DB Browser for SQLite or stop the Uvicorn server, then re-run.")
                sys.exit(1)

        store = RuleStore()
        store.init_schema()
        print(f"✅ Database successfully reset and re-initialized at: {store.db_path}")
        return

    ws = RunWorkspace(
        run_id=getattr(args, "run_id", None) or (get_latest_run_id() if args.command != "ingest" else None),
        cu_id=getattr(args, "cu_id", None)
    )

    if args.command == "ingest":
        from src.bsdc_engine.io.sharepoint import SharePointClient
        client = SharePointClient()

        downloaded = []
        cu_id = _resolve_cu_id(getattr(args, "cu_id", None), ws)
        sp_path = getattr(args, "sp_path", None)

        if cu_id or sp_path:
            mapping_p, matrix_p, actual_p, raw_p = None, None, None, None

            if sp_path:
                mapping_p = f"{sp_path}/07 Team Folders/Mapping"
                matrix_p = f"{sp_path}/07 Team Folders/Matrix"
                actual_p = f"{sp_path}/07 Team Folders/Actual_Sharetec"
                raw_p = f"{sp_path}/03 Info From CU"
            elif cu_id:
                auto_paths = client.resolve_cu_paths(cu_id)
                if auto_paths:
                    mapping_p = auto_paths.get("mapping_path")
                    matrix_p = auto_paths.get("matrix_path")
                    actual_p = auto_paths.get("actual_path")
                    raw_p = auto_paths.get("raw_data_path")

            if mapping_p:
                downloaded.extend(client.fetch_paths([mapping_p], output_dir=ws.mapping_dir, cu_id=cu_id, folder_type="mapping"))
            if matrix_p:
                downloaded.extend(client.fetch_paths([matrix_p], output_dir=ws.matrix_dir, cu_id=cu_id, folder_type="matrix"))
            if actual_p:
                downloaded.extend(client.fetch_paths([actual_p], output_dir=ws.actual_dir, cu_id=cu_id, folder_type="actual"))
            if raw_p:
                downloaded.extend(client.fetch_paths([raw_p], output_dir=ws.raw_dir, cu_id=cu_id, folder_type="raw"))

        elif args.paths:
            for path_str in args.paths:
                raw_downloaded = client.fetch_paths([path_str], output_dir=ws.raw_dir, cu_id=cu_id)
                for f in raw_downloaded:
                    if f.exists():
                        downloaded.append(_organize_file(f, ws))
        else:
            print("❌ Please provide either --cu-id, --sp-path, or --paths")
            sys.exit(1)

        print(f"✅ Ingest completed. Downloaded {len(downloaded)} files into workspace [{ws.run_id}].")

    elif args.command == "validate-mapping":
        from src.bsdc_engine.validate.mapping import MappingValidator

        cu_id = _resolve_cu_id(getattr(args, "cu_id", None), ws)
        if cu_id and ws.mapping_dir.exists():
            clean_cu = re.sub(r'[^a-z0-9]', '', cu_id.lower())
            for f in list(ws.mapping_dir.glob("*.xlsx")):
                if "mapping" in f.name.lower():
                    clean_fname = re.sub(r'[^a-z0-9]', '', f.name.lower())
                    if clean_cu not in clean_fname:
                        f.unlink(missing_ok=True)

        validator = MappingValidator(raw_dir=ws.mapping_dir, output_report_dir=ws.qa_reports_dir)
        is_passed, errors = validator.validate()
        status_symbol = "✅" if is_passed else "⚠️"
        print(f"{status_symbol} Mapping validation completed. Valid: {is_passed}. Errors found: {len(errors)}")

    elif args.command == "convert":
        from src.bsdc_engine.io.excel_converter import ExcelConverter
        converter = ExcelConverter(output_dir=ws.csv_dir)
        csv_files = converter.convert_all_in_dir(input_dir=ws.raw_dir)
        print(f"✅ Conversion completed. Generated {len(csv_files)} CSV files in {ws.csv_dir}")

    elif args.command == "parse-rules":
        from src.bsdc_engine.rules.parser import parse_all_mapping_sheets
        cu_id = _resolve_cu_id(getattr(args, "cu_id", None), ws)
        parse_all_mapping_sheets(raw_dir=ws.mapping_dir, cu_id=cu_id)
        print(f"✅ Rule parsing completed. Extracted rules from {ws.mapping_dir} into SQLite database.")

    elif args.command == "ai-parse":
        from src.bsdc_engine.rulegen.drafter import RuleDrafter
        drafter = RuleDrafter()
        count = drafter.draft_pending_rules()
        print(f"✅ AI Drafting completed. Processed {count} rules via Gemini.")

    elif args.command == "export-qa":
        from src.bsdc_engine.report.rule_verification import export_rule_verification_report
        cu_id = _resolve_cu_id(getattr(args, "cu_id", None), ws)
        export_rule_verification_report(output_dir=ws.qa_reports_dir, cu_id=cu_id)

    elif args.command == "apply-qa":
        from src.bsdc_engine.rules.decisions import apply_qa_decisions
        report_path = ws.qa_reports_dir / args.report_file
        apply_qa_decisions(reviewed_report_path=report_path)

    elif args.command == "generate":
        from src.bsdc_engine.generate.builders import TransformationBuilder
        from src.bsdc_engine.config import settings

        cu_id = _resolve_cu_id(getattr(args, "cu_id", None), ws)

        out_dir = getattr(ws, "transformed_dir", ws.reconciliation_dir)
        db_file = getattr(ws, "db_path", None) or settings.db_path

        raw_input_dirs = [ws.csv_dir, ws.raw_dir]

        builder = TransformationBuilder(
            raw_data_dir=raw_input_dirs,
            output_dir=out_dir,
            db_path=db_file
        )
        results = builder.generate_all(cu_id=cu_id)
        print(f"✅ Transformation completed. Generated {len(results)} tables into {out_dir}.")

    elif args.command == "verify":
        import polars as pl
        from src.bsdc_engine.verify.comparator import DataComparator
        from src.bsdc_engine.verify.formats import FormatValidator
        from src.bsdc_engine.verify.aggregates import AggregateChecker
        from src.bsdc_engine.verify.reporter import VerificationReporter

        exp_dir = getattr(ws, "reconciliation_dir", None) or (ws.out_dir / "reconciliation")
        act_dir = ws.actual_dir
        act_dir.mkdir(parents=True, exist_ok=True)

        if args.section_name:
            target_files = [exp_dir / f"{args.section_name}.csv"]
        else:
            target_files = list(exp_dir.glob("*.csv"))

        if not target_files:
            print(f"❌ No Expected CSV files found in: {exp_dir}")
            sys.exit(1)

        all_section_results = []

        for exp_file in target_files:
            sec_stem = exp_file.stem
            act_file = act_dir / f"{sec_stem}.csv"

            if not act_file.exists():
                print(f"⚠️ Skipping [{sec_stem}]: File missing in actual directory {act_dir}")
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

        VerificationReporter.generate_combined_report(all_section_results, output_dir=ws.test_output_dir)

    elif args.command == "assemble-worksheet":
        import polars as pl
        from src.bsdc_engine.verify.comparator import DataComparator
        from src.bsdc_engine.verify.formats import FormatValidator
        from src.bsdc_engine.verify.worksheet_assembler import assemble_verification_worksheet

        exp_dir = getattr(ws, "reconciliation_dir", None) or (ws.out_dir / "reconciliation")
        act_dir = ws.actual_dir

        target_files = list(exp_dir.glob("*.csv"))
        if not target_files:
            print(f"❌ No Expected CSV files found for worksheet assembly in: {exp_dir}")
            sys.exit(1)

        cu_id = _resolve_cu_id(getattr(args, "cu_id", None), ws) or "CU"

        verification_results = []
        for exp_file in target_files:
            sec_stem = exp_file.stem
            act_file = act_dir / f"{sec_stem}.csv"

            if not act_file.exists():
                continue

            df_exp = pl.read_csv(exp_file, infer_schema_length=0)
            df_act = pl.read_csv(act_file, infer_schema_length=0)

            key_columns = detect_key_columns(df_exp.columns, sec_stem)
            compare_columns = df_exp.columns

            exp_map = KeyMatcher.build_record_map(df_exp, key_columns, compare_columns)
            act_map = KeyMatcher.build_record_map(df_act, key_columns, compare_columns)

            mismatches = DataComparator.compare_maps(exp_map, act_map, compare_columns)
            fmt_issues = FormatValidator.validate_field_formats(act_map)

            verification_results.append({
                "section_name": sec_stem,
                "mismatches": mismatches,
                "fmt_issues": fmt_issues
            })

        worksheet_path = assemble_verification_worksheet(
            ws=ws,
            cu_id=cu_id,
            verification_results=verification_results,
            act_dir=act_dir,
            sp_template_relative_path=args.sp_template_path
        )

        dest_path = ws.test_output_dir / Path(worksheet_path).name
        shutil.move(str(worksheet_path), str(dest_path))
        worksheet_path = dest_path

        print(f"✅ Worksheet HTML successfully generated for [{cu_id}] at: {worksheet_path}")


if __name__ == "__main__":
    main()