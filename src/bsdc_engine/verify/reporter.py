import re
from pathlib import Path
from typing import List, Dict, Any
import pandas as pd


class VerificationReporter:

    @staticmethod
    def generate_combined_report(
        section_results: List[Dict[str, Any]],
        output_dir: Path = Path("test-output"),
    ):
        """
        Generate Extent Reports HTML with Left Navigation Sidebar.
        Separates Row/Aggregate Count checks from Row-Level Data Discrepancies table.
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        sidebar_items_html = ""
        content_panes_html = ""
        max_html_rows = 200

        for idx, res in enumerate(section_results):
            sec_name = res["section_name"]
            exp_rows = res["expected_rows"]
            act_rows = res["actual_rows"]
            mismatches = res.get("mismatches", [])
            fmt_issues = res.get("fmt_issues", [])
            agg_issues = res.get("agg_issues", [])

            # Clean display title for report
            clean_title = (
                sec_name.replace("Expected_", "")
                .replace("MEDICOOP_", "")
                .replace("_(MB_Table)", "")
                .replace("_(DP_Table)", "")
                .replace("_(LN_Table)", "")
                .replace(" Shares_and_Share_Drafts", "")
                .strip()
            )
            clean_title = re.sub(r'[\\/*?:"<>|]', "_", clean_title).upper()

            csv_filename = f"{clean_title}_Discrepancies.csv"
            csv_path = output_dir / csv_filename

            # Strictly separate field-level record discrepancies from aggregate/row count issues
            field_discrepancies = mismatches + fmt_issues
            data_issue_count = len(field_discrepancies)

            has_row_mismatch = (exp_rows != act_rows) or len(agg_issues) > 0
            is_failed = (data_issue_count > 0) or has_row_mismatch

            # 1. Export 100% of field discrepancies to individual CSV file
            if field_discrepancies:
                df = pd.DataFrame(field_discrepancies)
                df.to_csv(csv_path, index=False, encoding="utf-8-sig")
            elif csv_path.exists():
                csv_path.unlink()  # Delete old CSV if no discrepancies found

            # 2. Status badges and HTML active state
            is_active = "active" if idx == 0 else ""
            display_style = "block" if idx == 0 else "none"
            badge_class = "badge-fail" if is_failed else "badge-pass"
            badge_text = "Fail" if is_failed else "Pass"

            # Build Sidebar Item
            sidebar_items_html += f"""
            <div class="nav-item {is_active}" id="nav-item-{idx}" onclick="switchTab({idx})">
                <div class="nav-info">
                    <div class="nav-title">TABLE: {clean_title}</div>
                    <div class="nav-subtitle">Issues: {data_issue_count} | Rows: {act_rows}</div>
                </div>
                <span class="badge {badge_class}">{badge_text}</span>
            </div>
            """

            # 3. Build Table Rows for Right Pane (Excludes AGGREGATE, strictly field level)
            display_items = field_discrepancies[:max_html_rows]
            table_rows_html = ""
            for row_idx, item in enumerate(display_items, start=1):
                table_rows_html += f"""
                <tr>
                    <td style="text-align: center;"><b>{row_idx}</b></td>
                    <td><b>{item.get('key', '')}</b></td>
                    <td style="color: #0B5ED7; font-weight: 500;">{item.get('column', '')}</td>
                    <td style="color: #D9534F;">{item.get('expected', '')}</td>
                    <td>{item.get('actual', '')}</td>
                    <td style="color: #D9534F; font-weight: bold;">❌ {item.get('issue', 'VARIANCE')}</td>
                </tr>
                """

            truncation_note = ""
            if data_issue_count > max_html_rows:
                truncation_note = f"""
                <p style="color: #6c757d; font-style: italic; margin-top: 10px; font-size: 12px;">
                    * Showing first {max_html_rows} of {data_issue_count} field discrepancies. Refer to <b>{csv_filename}</b> for the complete dataset.
                </p>
                """

            # Row count mismatch warning badge inside the info box
            row_count_alert = ""
            if has_row_mismatch:
                row_count_alert = '<span style="color: #f64e60; font-weight: 700; margin-left: 15px;">⚠️ ROW COUNT MISMATCH</span>'

            # Build Content Pane
            o, c = "<", ">"
            csv_link_html = f' (Full details in CSV: {o}a href="{csv_filename}" target="_blank"{c}{o}b{c}{csv_filename}{o}/b{c}{o}/a{c} )' if field_discrepancies else ""
            content_panes_html += f"""
            <div class="tab-pane" id="tab-pane-{idx}" style="display: {display_style};">
                <div class="pane-header">
                    <h2 class="section-title">TABLE: {clean_title}</h2>
                    <span class="badge-large {badge_class}">{badge_text}</span>
                </div>
                <div class="info-box">
                    <div><b>Count:</b> Total Row Expected = {exp_rows} | Total Row Actual = {act_rows}</div>
                    <div>{row_count_alert}</div>
                </div>
                <div class="details-meta">
                    <b>Detail {data_issue_count} Data Discrepancies</b> {csv_link_html}:
                </div>
                <div class="table-container">
                    <table>
                        <thead>
                            <tr>
                                <th style="width: 60px; text-align: center;">NO.</th>
                                <th style="width: 180px;">KEY</th>
                                <th style="width: 220px;">COLUMN NAME</th>
                                <th>EXPECTED (DATA)</th>
                                <th>ACTUAL (SHARETEC)</th>
                                <th style="width: 180px;">ISSUES</th>
                            </tr>
                        </thead>
                        <tbody>
                            {table_rows_html if table_rows_html else '<tr><td colspan="6" style="text-align:center; color:#2ed573; padding:20px;"><b>✅ No field discrepancies found. All data matched perfectly!</b></td></tr>'}
                        </tbody>
                    </table>
                </div>
                {truncation_note}
            </div>
            """

        master_html_path = output_dir / "ExtentDataReport.html"
        full_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Data Reconciliation Verification Master Report</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; height: 100vh; overflow: hidden; background-color: #1e1e2d; color: #3f4254; }}
        
        .top-navbar {{ height: 50px; background-color: #1b1b28; border-bottom: 1px solid #2c2c3d; display: flex; align-items: center; justify-content: space-between; padding: 0 20px; color: #ffffff; font-weight: 600; font-size: 16px; }}
        
        .main-container {{ display: flex; height: calc(100vh - 50px); }}
        
        /* Left Sidebar Styling */
        .sidebar {{ width: 320px; background-color: #1e1e2d; border-right: 1px solid #2c2c3d; overflow-y: auto; flex-shrink: 0; }}
        .nav-item {{ padding: 14px 18px; border-bottom: 1px solid #2c2c3d; cursor: pointer; display: flex; align-items: center; justify-content: space-between; transition: background 0.2s; }}
        .nav-item:hover {{ background-color: #27273d; }}
        .nav-item.active {{ background-color: #2b2b40; border-left: 4px solid #3699ff; }}
        .nav-title {{ font-size: 13px; font-weight: 700; color: #e1e1e6; text-transform: uppercase; }}
        .nav-subtitle {{ font-size: 11px; color: #80808f; margin-top: 3px; }}
        
        /* Badges */
        .badge {{ padding: 3px 8px; border-radius: 4px; font-size: 10px; font-weight: 700; color: #ffffff; text-transform: uppercase; }}
        .badge-large {{ padding: 6px 16px; border-radius: 4px; font-size: 13px; font-weight: 700; color: #ffffff; text-transform: uppercase; }}
        .badge-pass {{ background-color: #1bc5bd; }}
        .badge-fail {{ background-color: #f64e60; }}
        
        /* Right Content Pane Styling */
        .content-area {{ flex: 1; background-color: #f5f8fa; padding: 25px; overflow-y: auto; }}
        .tab-pane {{ background: #ffffff; border-radius: 8px; box-shadow: 0 0 15px rgba(0,0,0,0.05); padding: 25px; }}
        .pane-header {{ display: flex; align-items: center; justify-content: space-between; border-bottom: 2px solid #eff2f5; padding-bottom: 15px; margin-bottom: 15px; }}
        .section-title {{ font-size: 18px; font-weight: 700; color: #181c32; }}
        
        .info-box {{ background-color: #e1f0ff; color: #004085; padding: 10px 15px; border-radius: 6px; font-size: 13px; margin-bottom: 15px; border-left: 4px solid #3699ff; display: flex; align-items: center; justify-content: space-between; }}
        .details-meta {{ font-size: 13px; color: #3f4254; margin-bottom: 12px; }}
        .details-meta a {{ color: #3699ff; text-decoration: none; }}
        .details-meta a:hover {{ text-decoration: underline; }}
        
        /* Table Styling */
        .table-container {{ overflow-x: auto; border: 1px solid #eff2f5; border-radius: 6px; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 13px; text-align: left; }}
        th {{ background-color: #4C4C5E; color: #ffffff; padding: 10px 12px; font-weight: 600; text-transform: uppercase; font-size: 11px; letter-spacing: 0.5px; }}
        td {{ padding: 8px 12px; border-bottom: 1px solid #eff2f5; color: #3f4254; word-break: break-word; }}
        tr:nth-child(even) {{ background-color: #fcfcfd; }}
        tr:hover {{ background-color: #f1f3f8; }}
    </style>
</head>
<body>
    <div class="top-navbar">
        <span>Data Reconciliation Verification Report</span>
        <span style="font-size: 12px; font-weight: 400; opacity: 0.75;">BSDC Automated Engine</span>
    </div>
    
    <div class="main-container">
        <!-- Sidebar Navigation -->
        <div class="sidebar">
            {sidebar_items_html}
        </div>
        
        <!-- Right Content Details -->
        <div class="content-area">
            {content_panes_html}
        </div>
    </div>

    <script>
        function switchTab(index) {{
            const navItems = document.querySelectorAll('.nav-item');
            navItems.forEach(item => item.classList.remove('active'));
            
            const tabPanes = document.querySelectorAll('.tab-pane');
            tabPanes.forEach(pane => pane.style.display = 'none');
            
            document.getElementById('nav-item-' + index).classList.add('active');
            document.getElementById('tab-pane-' + index).style.display = 'block';
        }}
    </script>
</body>
</html>
"""

        with open(master_html_path, "w", encoding="utf-8") as f:
            f.write(full_html)

        print(f"\n📊 Extent HTML Master Report updated at: {master_html_path}")