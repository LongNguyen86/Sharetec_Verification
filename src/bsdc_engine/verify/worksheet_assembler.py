import json
import re
from pathlib import Path
from datetime import datetime
import polars as pl
from bs4 import BeautifulSoup

from src.bsdc_engine.logging import get_logger

logger = get_logger(__name__)


def clean_key(text: str) -> str:
    """
    Ultra-clean field names or HTML text to pure alphanumeric lowercase.
    Examples: 'mb.mb-type' -> 'mbtype', 'mb-type' -> 'mbtype', 'Member Type' -> 'membertype'
    """
    if not text:
        return ""
    s = str(text).lower().strip().replace('\xa0', '')
    s = re.sub(r'^[a-z0-9_]+\.', '', s)
    return re.sub(r'[^a-z0-9]', '', s)


def strip_table_suffixes(name: str) -> str:
    """
    Remove common suffixes like 'table', 'discrepancies', 'discrepancy' to match section names accurately.
    """
    ck = clean_key(name)
    for suffix in ["discrepancies", "discrepancy", "discrepant", "table", "report", "mismatches", "variances"]:
        if ck.endswith(suffix):
            ck = ck[:-len(suffix)]
    return ck


def get_section_scope(sec_name: str) -> tuple[str, list[str]]:
    """
    Determine table code and specific sub-section keywords to map CSV to exact HTML Tab.
    """
    s = sec_name.lower()
    
    if "mb_table" in s or ("mb" in s and "table" in s and "dp" not in s and "ln" not in s):
        return "mb", ["panel-member", "member", "mb records", "member records"]
    
    if "dp_table" in s or "deposit" in s or "dp" in s:
        if "certificate" in s or "cd" in s:
            return "dp", ["panel-dep-cd", "dep-cd", "deposit cd", "certificate", "certificates"]
        elif "ira" in s:
            return "dp", ["panel-dep-ira", "dep-ira", "deposit ira", "ira"]
        elif "share" in s or "reg" in s or "sd" in s:
            return "dp", ["panel-dep-reg", "dep-reg", "deposit reg", "sd", "share", "regular"]
        return "dp", ["deposit"]
        
    if "ln_table" in s or "lending" in s or "loan" in s or "ln" in s:
        if "loc" in s:
            return "ln", ["panel-lending-loc", "lending-loc", "lending loc", "line of credit", "loc"]
        elif "mort" in s:
            return "ln", ["panel-lending-mort", "lending-mort", "lending mort", "mortgage", "mort"]
        elif "conv" in s:
            return "ln", ["panel-lending-conv", "lending-conv", "lending conv", "conventional", "conv"]
        return "ln", ["lending"]
        
    return "mb", ["member"]


def extract_field_samples(
    df_act: pl.DataFrame, 
    mismatches: list, 
    fmt_issues: list, 
    max_samples: int = 5
) -> tuple[dict[str, list[str]], set[str]]:
    """
    Extract top max_samples (5) rows directly from CSV in original order (indices 0..4).
    Comprehensively extracts failed field keys from string or dict formats.
    """
    df_top = df_act.head(max_samples)

    samples_per_field = {}
    for col in df_act.columns:
        samples = []
        for i in range(max_samples):
            if i < len(df_top):
                val = df_top[col][i]
                str_val = "" if val is None else str(val).strip()
                samples.append(str_val)
            else:
                samples.append("")

        ck = clean_key(col)
        if ck:
            samples_per_field[ck] = samples

    known_keys = set(samples_per_field.keys())
    failed_fields = set()

    for item in list(mismatches) + list(fmt_issues):
        if isinstance(item, str):
            ck = clean_key(item)
            if ck in known_keys:
                failed_fields.add(ck)
        elif isinstance(item, dict):
            for v_val in item.values():
                if v_val is not None:
                    ck = clean_key(str(v_val))
                    if ck in known_keys:
                        failed_fields.add(ck)

    return samples_per_field, failed_fields


def assemble_verification_worksheet(
    ws,
    cu_id: str,
    verification_results: list[dict],
    act_dir: Path,
    sp_template_relative_path: str | None = None
) -> Path:
    """
    Task 33: Populate HTML Verification Worksheet with extracted samples and status dropdowns.
    Scans all CSV files and discrepancy files on disk to accurately set STATUS = Fail.
    """
    local_template = Path("workspace/templates/Data_Verification_Procedure_Guide_V7.html")
    output_report_path = ws.qa_reports_dir / f"Data_Verification_Worksheet_{cu_id}.html"

    if not local_template.exists():
        logger.error(f"Template file not found at: {local_template}")
        return output_report_path

    raw_html = local_template.read_text(encoding="utf-8")
    soup = BeautifulSoup(raw_html, "html.parser")

    # 1. Update Header Metadata
    cu_inputs = soup.find_all("input", attrs={"placeholder": lambda v: v and "enter cu name" in v.lower()}) \
                or soup.find_all("input", attrs={"id": lambda v: v and "cu" in v.lower()})
    for inp in cu_inputs:
        inp["value"] = cu_id

    date_inputs = soup.find_all("input", attrs={"type": "date"}) or soup.find_all("input", attrs={"value": lambda v: v and "/" in v})
    today_str = datetime.now().strftime("%Y-%m-%d")
    for d_inp in date_inputs:
        d_inp["value"] = today_str

    # Map verification results for quick lookup
    verif_map = {}
    for res in verification_results:
        s_name = (
            res.get("section_name") or 
            res.get("section") or 
            res.get("table_name") or 
            res.get("table") or 
            res.get("name") or ""
        )
        if s_name:
            verif_map[s_name] = res
            verif_map[clean_key(s_name)] = res
            verif_map[strip_table_suffixes(s_name)] = res

    sections_payload = []

    # 2. Scan ALL CSV files directly in act_dir
    csv_files = sorted([f for f in act_dir.glob("*.csv") if "discrepancies" not in f.name.lower()])
    for csv_file in csv_files:
        sec_name = csv_file.stem
        base_sec_name = strip_table_suffixes(sec_name)

        res = verif_map.get(sec_name) or verif_map.get(clean_key(sec_name)) or verif_map.get(base_sec_name) or {}
        
        mismatches = res.get("mismatches", []) or res.get("discrepancies", []) or res.get("variances", []) or []
        fmt_issues = res.get("fmt_issues", []) or res.get("format_issues", []) or []

        table_code, scope_keywords = get_section_scope(sec_name)
        df_act = pl.read_csv(csv_file, infer_schema_length=0)
        samples_by_field, failed_fields = extract_field_samples(
            df_act, mismatches, fmt_issues, max_samples=5
        )

        known_sample_keys = set(samples_by_field.keys())

        # Scrape discrepancy CSV files on disk to guarantee capturing all fails
        search_paths = [
            act_dir, 
            act_dir.parent, 
            getattr(ws, "qa_reports_dir", None), 
            getattr(ws, "out_dir", None),
            getattr(ws, "run_dir", None),
            Path("workspace"),
            Path("test-output"),
            Path(".")
        ]
        for sp in search_paths:
            if sp and sp.exists():
                for disc_f in sp.rglob("*.csv"):
                    disc_stem_clean = strip_table_suffixes(disc_f.stem)
                    is_match = (
                        "discrep" in disc_f.name.lower() or "mismatch" in disc_f.name.lower() or "variance" in disc_f.name.lower()
                    ) and (
                        base_sec_name in disc_stem_clean or disc_stem_clean in base_sec_name or (table_code == "mb" and "mb" in disc_stem_clean)
                    )
                    if is_match:
                        try:
                            df_disc = pl.read_csv(disc_f, infer_schema_length=0)
                            for col_c in df_disc.columns:
                                for row_val in df_disc[col_c]:
                                    if row_val is not None and str(row_val).strip():
                                        ck = clean_key(str(row_val))
                                        if ck in known_sample_keys:
                                            failed_fields.add(ck)
                        except Exception as ex:
                            logger.warning(f"Error reading discrepancy CSV {disc_f}: {ex}")

        sections_payload.append({
            "section": sec_name,
            "table_code": table_code,
            "keywords": scope_keywords,
            "samples": samples_by_field,
            "failed": list(failed_fields)
        })

    # 3. Dynamic JS Payload Injection
    updated_html = str(soup)
    
    payload_json = json.dumps({
        "cu_id": cu_id,
        "sections": sections_payload
    }, indent=2)

    injection_script = f"""
<script id="bsdc-auto-populated-data">
  window.BSDC_AUTOMATED_DATA = {payload_json};
  (function() {{
    function cleanKey(txt) {{
      if (!txt) return "";
      var s = txt.toLowerCase().trim();
      s = s.replace(/^[a-z0-9_]+\\./, "");
      return s.replace(/[^a-z0-9]/g, "");
    }}

    function getLocalCardInfo(row) {{
      var table = row.closest("table");
      if (!table) return {{ tabId: "", cardTitle: "" }};
      var card = table.closest(".section-card, .card, .tab-panel, section, [id*='sec-']");
      if (!card) return {{ tabId: "", cardTitle: "" }};
      var tabPanel = card.closest(".tab-panel");
      var tabId = tabPanel ? (tabPanel.id || "").toLowerCase() : "";
      var header = card.querySelector(".section-header, h1, h2, h3, h4, h5, .card-header, .table-title");
      var cardTitle = (tabId + " " + (card.id || "") + " " + (header ? header.textContent : "")).toLowerCase();
      return {{ tabId: tabId, cardTitle: cardTitle }};
    }}

    function isCandidateMatch(cand, info) {{
      var tc = cand.table_code;
      var tabId = info.tabId;
      var cardTitle = info.cardTitle;

      if (tc === "dp") {{
        var candIsCD = cand.keywords.some(function(k) {{ return k === "panel-dep-cd" || k === "dep-cd" || k === "deposit cd" || k.includes("certif"); }});
        var candIsIRA = cand.keywords.some(function(k) {{ return k === "panel-dep-ira" || k === "dep-ira" || k === "deposit ira" || k === "ira"; }});
        var candIsReg = cand.keywords.some(function(k) {{ return k === "panel-dep-reg" || k === "dep-reg" || k === "deposit reg" || k === "sd" || k === "share" || k === "regular"; }});

        var cardIsCD = tabId === "panel-dep-cd" || cardTitle.includes("dep-cd") || cardTitle.includes("certificates of deposit");
        var cardIsIRA = tabId === "panel-dep-ira" || cardTitle.includes("dep-ira") || cardTitle.includes("deposit ira") || cardTitle.includes("ira owner") || cardTitle.includes("ira tran");
        var cardIsReg = tabId === "panel-dep-reg" || cardTitle.includes("dep-reg") || cardTitle.includes("deposit reg") || cardTitle.includes("regular shares") || cardTitle.includes("share draft");

        if (candIsCD && !cardIsCD) return false;
        if (candIsIRA && !cardIsIRA) return false;
        if (candIsReg && !cardIsReg) return false;
      }}

      if (tc === "ln") {{
        var candIsLOC = cand.keywords.some(function(k) {{ return k.includes("loc"); }});
        var candIsMort = cand.keywords.some(function(k) {{ return k.includes("mort"); }});
        var candIsConv = cand.keywords.some(function(k) {{ return k.includes("conv"); }});

        var cardIsLOC = tabId === "panel-lending-loc" || cardTitle.includes("lending-loc") || cardTitle.includes("lines of credit");
        var cardIsMort = tabId === "panel-lending-mort" || cardTitle.includes("lending-mort") || cardTitle.includes("mortgage loans");
        var cardIsConv = tabId === "panel-lending-conv" || cardTitle.includes("lending-conv") || cardTitle.includes("conventional loans");

        if (candIsLOC && !cardIsLOC) return false;
        if (candIsMort && !cardIsMort) return false;
        if (candIsConv && !cardIsConv) return false;
      }}

      if (tc === "mb") {{
        var cardIsMB = tabId === "panel-member" || cardTitle.includes("member") || cardTitle.includes("mb");
        if (!cardIsMB) return false;
      }}

      for (var k = 0; k < cand.keywords.length; k++) {{
        if (cardTitle.indexOf(cand.keywords[k]) !== -1) {{
          return true;
        }}
      }}
      return false;
    }}

    function applyAutomatedData() {{
      var data = window.BSDC_AUTOMATED_DATA;
      if (!data || !data.sections) return;

      var rows = document.querySelectorAll("tr");
      rows.forEach(function(row) {{
        var cells = row.querySelectorAll("td, th");
        if (cells.length < 3) return;

        var cell0Clean = cleanKey(cells[0].textContent);
        if (!cell0Clean) return;

        var fieldNameClean = cells[1] ? cleanKey(cells[1].textContent) : "";

        var stecElem = row.querySelector(".col-stec, .stec-field") || cells[2];
        var rawField = stecElem ? stecElem.textContent : "";
        var fieldKey = cleanKey(rawField);
        if (!fieldKey && !fieldNameClean) return;

        var info = getLocalCardInfo(row);

        if (info.cardTitle.indexOf("progress") !== -1 && cell0Clean !== "progress") return;

        var candidates = [];
        for (var s = 0; s < data.sections.length; s++) {{
          if (data.sections[s].table_code === cell0Clean) {{
            candidates.push(data.sections[s]);
          }}
        }}

        if (candidates.length === 0) return;

        var matchedSection = null;
        for (var c = 0; c < candidates.length; c++) {{
          if (isCandidateMatch(candidates[c], info)) {{
            matchedSection = candidates[c];
            break;
          }}
        }}

        if (!matchedSection || !matchedSection.samples) return;

        var samples = matchedSection.samples[fieldKey] || matchedSection.samples[fieldNameClean];
        if (samples && Array.isArray(samples)) {{
          var inputs = Array.from(row.querySelectorAll("input.mb-val"));
          if (inputs.length === 0) {{
            inputs = Array.from(row.querySelectorAll("input")).filter(function(inp) {{
              var ph = (inp.placeholder || "").toLowerCase();
              var nm = (inp.name || "").toLowerCase();
              var id = (inp.id || "").toLowerCase();
              return !ph.includes("jira") && !ph.includes("note") && 
                     !nm.includes("jira") && !nm.includes("note") && 
                     !id.includes("jira") && !id.includes("note");
            }});
          }}

          samples.forEach(function(val, idx) {{
            if (inputs[idx]) {{
              inputs[idx].value = val;
              inputs[idx].dispatchEvent(new Event('input', {{ bubbles: true }}));
              inputs[idx].dispatchEvent(new Event('change', {{ bubbles: true }}));
            }}
          }});

          var select = row.querySelector("select.status-sel, select");
          if (select) {{
            var failedList = matchedSection.failed || [];
            var isFail = (fieldKey && failedList.indexOf(fieldKey) !== -1) || 
                         (fieldNameClean && failedList.indexOf(fieldNameClean) !== -1);
            var targetStatus = isFail ? "fail" : "pass";

            for (var optIdx = 0; optIdx < select.options.length; optIdx++) {{
              var optText = (select.options[optIdx].text || "").toLowerCase();
              var optVal = (select.options[optIdx].value || "").toLowerCase();
              if (optText.includes(targetStatus) || optVal.includes(targetStatus)) {{
                select.selectedIndex = optIdx;
                select.options[optIdx].selected = true;
                if (typeof setStatus === "function") {{
                  setStatus(select);
                }} else {{
                  select.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
                break;
              }}
            }}
          }}
        }}
      }});
    }}

    applyAutomatedData();

    if (document.readyState === "loading") {{
      document.addEventListener("DOMContentLoaded", applyAutomatedData);
    }}

    document.addEventListener("click", function() {{
      setTimeout(applyAutomatedData, 100);
      setTimeout(applyAutomatedData, 300);
    }});

    var attempts = 0;
    var interval = setInterval(function() {{
      applyAutomatedData();
      attempts++;
      if (attempts >= 10) clearInterval(interval);
    }}, 500);
  }})();
</script>
"""
    if "</body>" in updated_html:
        updated_html = updated_html.replace("</body>", f"{injection_script}\n</body>")
    else:
        updated_html += injection_script

    logger.info(f"Successfully processed worksheet for [{cu_id}]. Updated discrepancy key mapping for Fail status.")

    output_report_path.parent.mkdir(parents=True, exist_ok=True)
    output_report_path.write_text(updated_html, encoding="utf-8")
    return output_report_path