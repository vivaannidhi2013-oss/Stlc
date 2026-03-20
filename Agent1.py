"""
=============================================================
 STLC Agentic Framework — Agent 1: Requirement Analyzer
 VERSION 3.0 — REFERENCE-QUALITY OUTPUT
=============================================================
 Key upgrades in this version:
   ✅ Domain-aware prompt — understands 
   ✅ Assumption Log generated before test cases
   ✅ Module-based test organisation (mirrors the logic layers)
   ✅ Fallthrough/Priority cross-field interaction module
   ✅ Data Integrity module (Kafka-specific: offsets, no mutation, no loss)
   ✅ VERIFY annotations on ambiguous edge cases
   ✅ Realistic domain-specific test 
   ✅ Suggested execution order based on risk
   ✅ All 6 field states declared explicitly in every test case
   ✅ Markdown output mirrors the reference document style

 Install dependencies:
   pip install boto3 pdfplumber python-docx

 Usage:
   python agent1_requirement_analyzer_v3.py
   → You will be prompted to enter the path to your requirement file.
=============================================================
"""

import boto3
import json
import os
import time
from datetime import datetime
from botocore.exceptions import ClientError

try:
    import pdfplumber
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

try:
    from docx import Document as DocxDocument
    DOCX_SUPPORT = True
except ImportError:
    DOCX_SUPPORT = False


# ------------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------------
AWS_REGION  = "eu-west-1"
MODEL_ID    = "eu.anthropic.claude-3-5-sonnet-20240620-v1:0"
MAX_RETRIES = 3
RETRY_DELAY = 5


# ------------------------------------------------------------------
# BEDROCK CLIENT
# ------------------------------------------------------------------
client = boto3.client("bedrock-runtime", region_name=AWS_REGION)


# ==================================================================
# SECTION 1: FILE READERS (.txt / .pdf / .docx)
# ==================================================================

def read_txt(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read().strip()

def read_pdf(filepath):
    if not PDF_SUPPORT:
        raise ImportError("Run: pip install pdfplumber")
    parts = []
    with pdfplumber.open(filepath) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text()
            if text:
                parts.append(f"--- Page {i+1} ---\n{text}")
    if not parts:
        raise ValueError(f"No text extracted from: {filepath}")
    return "\n\n".join(parts).strip()

def read_docx(filepath):
    if not DOCX_SUPPORT:
        raise ImportError("Run: pip install python-docx")
    doc = DocxDocument(filepath)
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip()).strip()

def read_requirement_file(filepath):
    filepath = filepath.strip("'\"")
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")
    ext = os.path.splitext(filepath)[1].lower()
    readers = {".txt": read_txt, ".pdf": read_pdf, ".docx": read_docx}
    if ext not in readers:
        raise ValueError(f"Unsupported file type '{ext}'. Supported: .txt .pdf .docx")
    print(f"📂 Reading {ext.upper()} file: {filepath}")
    content = readers[ext](filepath)
    print(f"✅ Read {len(content)} characters\n")
    return content


# ==================================================================
# SECTION 2: THE CORE PROMPT
# This is the most important part. It instructs Claude to think
# like a domain expert, not just a generic test case generator.
# ==================================================================

def build_prompt(requirement: str) -> str:
    return f"""
You are a Principal QA Engineer with deep expertise in:
- Kafka Streams and event-driven microservice testing
- Financial domain systems (IBAN, BIC, payment routing)
- Equivalence partitioning, boundary value analysis, and decision table testing
- Distributed system testing (offset management, message integrity, pod lifecycle)

You will be given a software requirement. Your job is to produce a COMPLETE, PROFESSIONAL
test case document that a QA team can execute immediately — like a senior engineer wrote it.

============================
REQUIREMENT:
============================
{requirement}
============================

Follow this EXACT thinking and output process — do not skip any step:

─────────────────────────────────────────────────────────────────
STEP 1: DECOMPOSE THE REQUIREMENT
─────────────────────────────────────────────────────────────────
Read the requirement carefully. Identify:
  - The system type (e.g., Kafka Streams, REST API, batch job, UI)
  - The exact routing/branching/decision logic with all conditions
  - Every input field involved (name, type, where it's checked)
  - The possible output paths (e.g., Topic A vs Topic B, success vs error)
  - The priority/fallthrough order if multiple conditions exist

─────────────────────────────────────────────────────────────────
STEP 2: SURFACE ASSUMPTIONS
─────────────────────────────────────────────────────────────────
Before writing test cases, identify ambiguities in the requirement that could
flip expected results if the assumption is wrong. For each assumption:
  - Give it an ID (A1, A2, ...)
  - State the assumption clearly
  - State the impact: which test cases would change if the assumption is wrong

─────────────────────────────────────────────────────────────────
STEP 3: ORGANISE INTO LOGICAL MODULES
─────────────────────────────────────────────────────────────────
Group test cases by the LOGIC LAYER being tested, not by positive/negative.
Each condition branch in the requirement becomes its own module.

Standard modules to always include:
  - One module per routing condition (e.g., IBAN check, BIC check, etc.)
  - A "Priority & Fallthrough" module — tests the interaction BETWEEN conditions
  - A "Data Integrity" module — tests system-level correctness (no message loss,
    no mutation, offset/commit behaviour, no spillover between outputs)

─────────────────────────────────────────────────────────────────
STEP 4: WRITE TEST CASES
─────────────────────────────────────────────────────────────────
For each test case:
  - Declare ALL relevant input fields explicitly (null or value) — never leave
    unrelated fields ambiguous. Readers must know the full system state.
  - Use REALISTIC, domain-appropriate test data:
      * IBANs: "IN12345678901234", "CH5600000001234567890", "GB29NWBK60161331926819"
      * BICs: "ABCDINXX", "XYZWCHUS", "HBUKGB4B" (5th+6th chars carry country code)
      * Use actual format conventions for the domain, not placeholders like "<valid_iban>"
  - For edge cases, add a VERIFY note: what the tester must confirm with the developer
  - Cover: positive routing, negative routing, boundary values, wrong-position matches,
    empty string vs null, case sensitivity, single-char values, full fallthrough chains,
    cross-side (debtor vs creditor) triggering

─────────────────────────────────────────────────────────────────
STEP 5: SUGGEST EXECUTION ORDER
─────────────────────────────────────────────────────────────────
Recommend in which order to execute modules, with a brief reason for each.
Highest-risk or most foundational logic should be validated first.

─────────────────────────────────────────────────────────────────
OUTPUT FORMAT — RETURN ONLY THIS JSON, NO OTHER TEXT
─────────────────────────────────────────────────────────────────

{{
  "app_context": {{
    "app_name": "<name of the system under test>",
    "system_type": "<e.g., Kafka Streams App, REST API, UI>",
    "routing_summary": "<one paragraph describing the routing logic clearly>",
    "input_fields": ["<field1>", "<field2>"],
    "output_destinations": ["<destination1>", "<destination2>"]
  }},
  "assumptions": [
    {{
      "id": "A1",
      "assumption": "<the assumption being made>",
      "impact_if_wrong": "<which test case IDs or behaviour would change>"
    }}
  ],
  "modules": [
    {{
      "module_id": "M1",
      "module_name": "<name of this logic layer, e.g., IBAN-Based Routing>",
      "description": "<what this module tests and why>",
      "test_cases": [
        {{
          "id": "TC001",
          "type": "positive",
          "title": "<clear, specific title>",
          "input_state": {{
            "<field1>": "<value or null>",
            "<field2>": "<value or null>"
          }},
          "expected_output": "<exact destination and routing outcome>",
          "verify_note": "<optional: what to confirm with dev, or null>"
        }}
      ]
    }}
  ],
  "execution_order": [
    {{
      "order": 1,
      "module_id": "M4",
      "reason": "<why this module should be executed first>"
    }}
  ],
  "summary": {{
    "total_test_cases": 0,
    "by_module": {{
      "M1": 0
    }}
  }}
}}

RULES — strictly follow these:
- Generate a MINIMUM of 10 test cases per routing-condition module
- Priority & Fallthrough module: minimum 8 test cases covering all chain combinations
- Data Integrity module: minimum 5 test cases (no loss, no mutation, no spillover, offset commit)
- Every input_state must list ALL relevant fields — never omit an unrelated field
- Test data must be specific and realistic for the domain (financial, HTTP, etc.)
- Include at least: lowercase variant, empty string, single character, wrong-position match
- Return ONLY valid JSON. Absolutely no text, explanation, or markdown outside the JSON.
"""


# ==================================================================
# SECTION 3: BEDROCK API CALLER WITH RETRY
# ==================================================================

def call_claude(prompt: str) -> str:
    native_request = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 8096,   # High limit — detailed test cases are verbose
        "temperature": 0.1,   # Very low — structured output must be consistent
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt}]
            }
        ]
    }
    body = json.dumps(native_request)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"⏳ Calling Claude via Bedrock (Attempt {attempt}/{MAX_RETRIES})...")
            response = client.invoke_model(modelId=MODEL_ID, body=body)
            result   = json.loads(response["body"].read())
            text     = result["content"][0]["text"]
            print("✅ Claude responded successfully.\n")
            return text
        except ClientError as e:
            print(f"⚠️  API Error on attempt {attempt}: {e}")
            if attempt < MAX_RETRIES:
                print(f"   Retrying in {RETRY_DELAY}s...")
                time.sleep(RETRY_DELAY)
            else:
                raise


# ==================================================================
# SECTION 4: RESPONSE PARSER
# ==================================================================

def parse_response(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text  = "\n".join(lines[1:-1]).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end])
            except Exception:
                pass
        print("❌ JSON parse failed. Raw snippet:\n", raw[:500])
        raise


# ==================================================================
# SECTION 5: MARKDOWN REPORT GENERATOR
# Produces a report that matches the style of the reference document.
# ==================================================================

def generate_markdown_report(data: dict, source_file: str) -> str:
    now     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ctx     = data.get("app_context", {})
    summary = data.get("summary", {})
    modules = data.get("modules", [])
    assumptions = data.get("assumptions", [])
    exec_order  = data.get("execution_order", [])

    total = sum(
        len(m.get("test_cases", [])) for m in modules
    )

    # ── Header ──────────────────────────────────────────────────────
    md  = f"# 🧪 Functional Test Cases — {ctx.get('app_name', 'System Under Test')}\n\n"
    md += "```\n"
    md += f"{'='*72}\n"
    md += f"  FUNCTIONAL TEST CASES — {ctx.get('app_name', '').upper()}\n"
    md += f"{'='*72}\n"
    md += f"App Type   : {ctx.get('system_type', 'N/A')}\n"
    md += f"Generated  : {now}\n"
    md += f"Source File: {os.path.basename(source_file)}\n"
    md += f"{'='*72}\n"
    md += f"Routing Summary:\n"
    for line in ctx.get("routing_summary", "").split(". "):
        if line.strip():
            md += f"  {line.strip()}.\n"
    md += f"\nOutput Destinations:\n"
    for dest in ctx.get("output_destinations", []):
        md += f"  → {dest}\n"
    md += f"{'='*72}\n"
    md += "```\n\n"

    # ── Assumption Log ───────────────────────────────────────────────
    md += "---\n\n"
    md += "## 📌 Assumption Log\n\n"
    md += "> Confirm these with Dev / BA **before** test execution. Wrong assumptions flip expected results.\n\n"
    md += "```\n"
    for a in assumptions:
        md += f"{a.get('id', '')} | {a.get('assumption', '')}\n"
        md += f"   | Impact if wrong: {a.get('impact_if_wrong', '')}\n\n"
    md += "```\n\n"

    # ── Test Cases by Module ─────────────────────────────────────────
    tc_global_counter = 0
    for module in modules:
        mod_name  = module.get("module_name", "Unknown Module")
        mod_desc  = module.get("description", "")
        tcs       = module.get("test_cases", [])
        mod_id    = module.get("module_id", "")

        md += "---\n\n"
        md += f"## {mod_id}: {mod_name}\n\n"
        if mod_desc:
            md += f"*{mod_desc}*\n\n"
        md += "```\n"

        for tc in tcs:
            tc_global_counter += 1
            tc_id    = tc.get("id", f"TC{tc_global_counter:03d}")
            tc_title = tc.get("title", "N/A")
            tc_type  = tc.get("type", "").upper()
            input_state = tc.get("input_state", {})
            expected    = tc.get("expected_output", "N/A")
            verify      = tc.get("verify_note")

            type_tag = {"POSITIVE": "     ", "NEGATIVE": "[NEG]", "EDGE_CASE": "[EDG]", "SECURITY": "[SEC]"}.get(tc_type, "     ")

            md += f"{tc_id} {type_tag} | {tc_title}\n"
            md += f"       Input  :\n"
            for field, value in input_state.items():
                display_val = f'"{value}"' if value not in [None, "null", "NULL"] else "null"
                md += f"                {field} = {display_val}\n"
            md += f"       Expect : {expected}\n"
            if verify:
                md += f"               VERIFY: {verify}\n"
            md += "\n"

        md += "```\n\n"

    # ── Suggested Execution Order ────────────────────────────────────
    md += "---\n\n"
    md += "## 🎯 Suggested Execution Order\n\n"
    md += "```\n"
    for item in exec_order:
        md += f"  {item.get('order', '')}. {item.get('module_id', '')} — {item.get('reason', '')}\n"
    md += "```\n\n"

    # ── Quick Reference Summary ──────────────────────────────────────
    md += "---\n\n"
    md += "## 📊 Quick Reference — Test Case Count by Module\n\n"
    md += "```\n"
    for module in modules:
        mod_name = module.get("module_name", "Unknown")
        count    = len(module.get("test_cases", []))
        tcs_list = module.get("test_cases", [])
        if tcs_list:
            first_id = tcs_list[0].get("id", "")
            last_id  = tcs_list[-1].get("id", "")
            md += f"  {module.get('module_id', '')} — {mod_name:<45}: {first_id} – {last_id}  ({count} test cases)\n"
    md += f"  {'─'*70}\n"
    md += f"  TOTAL{'':<62}: {total} test cases\n"
    md += "```\n\n"
    md += "---\n\n"
    md += f"*Generated by STLC Agentic Framework — Agent 1 | {now}*\n"

    return md


# ==================================================================
# SECTION 6: FILE SAVERS
# ==================================================================

def save_markdown(content: str, filename: str):
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"📄 Markdown report : {filename}")

def save_json(data: dict, filename: str):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"💾 JSON (Agent 2 input): {filename}")


# ==================================================================
# SECTION 7: MAIN AGENT RUNNER
# ==================================================================

def run_agent1(filepath: str) -> dict:
    print("\n" + "="*60)
    print("🤖 AGENT 1: Requirement Analyzer  |  v3.0")
    print("="*60 + "\n")

    requirement_text = read_requirement_file(filepath)
    prompt           = build_prompt(requirement_text)
    raw_response     = call_claude(prompt)

    print("🔍 Parsing structured output...")
    parsed = parse_response(raw_response)

    total = sum(len(m.get("test_cases", [])) for m in parsed.get("modules", []))
    print(f"✅ Generated {total} test cases across {len(parsed.get('modules', []))} modules.\n")

    md_report  = generate_markdown_report(parsed, filepath)
    base_name  = os.path.splitext(os.path.basename(filepath))[0]

    save_markdown(md_report, filename=f"{base_name}_test_cases.md")
    save_json(parsed,        filename=f"{base_name}_test_cases.json")

    print("\n" + "="*60)
    print("✅ Agent 1 complete!")
    print("="*60)

    return parsed


# ==================================================================
# ENTRY POINT
# ==================================================================

if __name__ == "__main__":
    print("\n" + "="*60)
    print(" STLC Agentic Framework — Agent 1  |  v3.0")
    print(" Supported: .txt  |  .pdf  |  .docx")
    print("="*60 + "\n")

    filepath = input("📂 Enter path to your requirement file: ").strip()
    run_agent1(filepath)
