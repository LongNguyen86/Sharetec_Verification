import json
import re
from pathlib import Path
from datetime import datetime
import polars as pl
from bs4 import BeautifulSoup
from src.bsdc_engine.logging import get_logger

logger = get_logger(__name__)


def clean_key(text: str) -> str:
    """Normalize text to alphanumeric lowercase, stripping table prefixes."""
    if not text:
        return ""
    s = str(text).lower().strip().replace('\xa0', '')
    s = re.sub(r'^[a-z0-9_]+\.', '', s)
    return re.sub(r'[^a-z0-9]', '', s)


def strip_table_suffixes(name: str) -> str:
    """Strip table/data processing suffixes from section titles."""
    ck = clean_key(name)
    suffixes = [
        "processeddata", "extracteddata", "processed_data", "extracted_data",
        "discrepancies", "discrepancy", "table", "report", "mismatches"
    ]
    changed = True
    while changed:
        changed = False
        for s in suffixes:
            if ck.endswith(s):
                ck = ck[:-len(s)]
                changed = True
                break
    return ck


def extract_table_code(sec_name: str, sample_fields: list[str]) -> str:
    """
    Dynamically extract table code from field prefixes (e.g., 'gl.gl-num' -> 'gl', 'mb.mb-num' -> 'mb').
    Falls back to first alphanumeric token from section name.
    """
    for f in sample_fields:
        if "." in f:
            prefix = f.split(".")[0].lower().strip()
            if len(prefix) <= 6:
                return prefix

    tokens = re.findall(r"[a-zA-Z]+", sec_name.lower())
    return tokens[0][:4] if tokens else "gen"


def assemble_verification_worksheet(
    ws,
    cu_id: str,
    verification_results: list[dict],
    act_dir: Path,
    sp_template_relative_path: str | None = None
) -> Path:
    """
    Populate HTML Verification Worksheet dynamically across any core banking section.
    """
    local_template = Path("workspace/templates/Data_Verification_Procedure_Guide_V7.html")
    output_report_path = ws.qa_reports_dir / f"Data_Verification_Worksheet_{cu_id}.html"

    if not local_template.exists():
        logger.error(f"Template file not found at: {local_template}")
        return output_report_path

    raw_html = local_template.read_text(encoding="utf-8")
    soup = BeautifulSoup(raw_html, "html.parser")

    # Update metadata fields
    for inp in soup.find_all("input", attrs={"placeholder": lambda v: v and "cu" in v.lower()}):
        inp["value"] = cu_id

    today_str = datetime.now().strftime("%Y-%m-%d")
    for d_inp in soup.find_all("input", attrs={"type": "date"}):
        d_inp["value"] = today_str

    verif_map = {res.get("section_name", ""): res for res in verification_results}
    sections_payload = []

    csv_files = sorted([f for f in act_dir.glob("*.csv") if "discrepancies" not in f.name.lower()])
    for csv_file in csv_files:
        sec_name = csv_file.stem
        res = verif_map.get(sec_name, {})
        mismatches = res.get("mismatches", [])
        fmt_issues = res.get("fmt_issues", [])

        df_act = pl.read_csv(csv_file, infer_schema_length=0)
        df_top = df_act.head(5)

        samples_by_field = {}
        for col in df_act.columns:
            samples = [str(df_top[col][i]) if i < len(df_top) and df_top[col][i] is not None else "" for i in range(5)]
            ck = clean_key(col)
            if ck:
                samples_by_field[ck] = samples

        failed_fields = set()
        for item in mismatches + fmt_issues:
            if isinstance(item, dict):
                for v in item.values():
                    if v:
                        failed_fields.add(clean_key(str(v)))

        table_code = extract_table_code(sec_name, list(df_act.columns))

        sections_payload.append({
            "section": sec_name,
            "table_code": table_code,
            "samples": samples_by_field,
            "failed": list(failed_fields),
        })

    payload_json = json.dumps({"cu_id": cu_id, "sections": sections_payload}, indent=2)

    injection_script = f"""
<script id="bsdc-auto-populated-data">
  window.BSDC_AUTOMATED_DATA = {payload_json};
  (function() {{
    function cleanKey(txt) {{
      if (!txt) return "";
      return txt.toLowerCase().trim().replace(/^[a-z0-9_]+\\./, "").replace(/[^a-z0-9]/g, "");
    }}

    function applyAutomatedData() {{
      var data = window.BSDC_AUTOMATED_DATA;
      if (!data || !data.sections) return;

      var rows = document.querySelectorAll("tr");
      rows.forEach(function(row) {{
        var cells = row.querySelectorAll("td, th");
        if (cells.length < 3) return;

        var fieldNameClean = cells[1] ? cleanKey(cells[1].textContent) : "";
        var stecElem = row.querySelector(".col-stec, .stec-field") || cells[2];
        var fieldKey = cleanKey(stecElem ? stecElem.textContent : "");
        if (!fieldKey && !fieldNameClean) return;

        data.sections.forEach(function(sec) {{
          var samples = sec.samples[fieldKey] || sec.samples[fieldNameClean];
          if (samples) {{
            var inputs = Array.from(row.querySelectorAll("input.mb-val, input[type='text']"));
            samples.forEach(function(val, idx) {{
              if (inputs[idx]) {{
                inputs[idx].value = val;
                inputs[idx].dispatchEvent(new Event('change', {{ bubbles: true }}));
              }}
            }});

            var select = row.querySelector("select.status-sel, select");
            if (select) {{
              var isFail = sec.failed.indexOf(fieldKey) !== -1 || sec.failed.indexOf(fieldNameClean) !== -1;
              select.value = isFail ? "fail" : "pass";
              select.dispatchEvent(new Event('change', {{ bubbles: true }}));
            }}
          }}
        }});
      }});
    }}

    if (document.readyState === "loading") {{
      document.addEventListener("DOMContentLoaded", applyAutomatedData);
    }} else {{
      applyAutomatedData();
    }}
  }})();
</script>
"""
    updated_html = str(soup)
    updated_html = updated_html.replace("</body>", f"{injection_script}\n</body>") if "</body>" in updated_html else updated_html + injection_script

    output_report_path.write_text(updated_html, encoding="utf-8")
    return output_report_path