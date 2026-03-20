"""
=============================================================
 STLC Agentic Framework — Agent 1: Requirement Analyzer
 VERSION 4.0 — MULTI-CALL PIPELINE (Truncation-Proof)
=============================================================
 Root cause of v3 failure:
   Claude was asked to generate 50+ test cases in one API call.
   The response hit the token ceiling and was cut mid-JSON.
   No amount of JSON repair can fix a physically truncated response.

 The fix — split into focused API calls:
   Call 1 → App context + Assumptions + Module plan (lightweight)
   Call 2 → Test cases for Module 1 only
   Call 3 → Test cases for Module 2 only
   Call N → Test cases for Module N only
   Final  → Assemble everything + generate Markdown

 Each call is small, focused, and never truncates.
 Claude also reasons better when asked about one thing at a time.

 Install:  pip install boto3 pdfplumber python-docx
 Run:      python agent1_requirement_analyzer_v4.py
=============================================================
"""

import boto3
import json
import os
import re
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
# SECTION 1: FILE READERS
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
# SECTION 2: BEDROCK CALLER (shared by all calls)
# ==================================================================

def call_claude(prompt: str, max_tokens: int = 2048) -> str:
    """
    Single Claude API call via Bedrock with retry.
    max_tokens kept intentionally small per call — each call is focused.
    """
    native_request = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "temperature": 0.1,
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt}]
            }
        ]
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.invoke_model(
                modelId=MODEL_ID,
                body=json.dumps(native_request)
            )
            result = json.loads(response["body"].read())
            return result["content"][0]["text"]
        except ClientError as e:
            print(f"   ⚠️  Attempt {attempt}/{MAX_RETRIES} failed: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
            else:
                raise


# ==================================================================
# SECTION 3: JSON PARSER (robust, handles minor LLM quirks)
# ==================================================================

def parse_json(raw: str, label: str = "") -> dict:
    """
    Parses JSON from Claude's response.
    Handles: markdown fences, trailing commas, leading/trailing text.
    Does NOT attempt to fix truncation — that's solved architecturally now.
    """
    text = raw.strip()

    # Strip markdown code fences
    if "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1]
            if text.lower().startswith("json"):
                text = text[4:]
        text = text.strip()

    # Extract outermost JSON object
    start = text.find("{")
    end   = text.rfind("}") + 1
    if start != -1 and end > start:
        text = text[start:end]

    # Fix trailing commas (the most common LLM JSON quirk)
    text = re.sub(r",\s*(\}|\])", r"\1", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        debug_file = f"debug_{label.replace(' ', '_')}.txt"
        with open(debug_file, "w", encoding="utf-8") as f:
            f.write(raw)
        print(f"\n❌ JSON parse failed for '{label}'")
        print(f"   Error: {e}")
        print(f"   Raw response saved to: {debug_file}")
        raise


# ==================================================================
# SECTION 4: CALL 1 — APP CONTEXT + ASSUMPTIONS + MODULE PLAN
#
# This call is deliberately lightweight.
# It only asks Claude to PLAN the modules — no test cases yet.
# Output: app context, assumptions, list of modules with descriptions.
# ==================================================================

PROMPT_PLAN = """
You are a Principal QA Engineer specialising in Kafka Streams, financial payment systems,
and distributed system testing.

Analyse the following requirement and produce a TEST PLAN — but do NOT write any test cases yet.

============================
REQUIREMENT:
============================
{requirement}
============================

Your output must contain exactly:

1. APP CONTEXT — What system is being tested, what are the inputs and outputs
2. ASSUMPTIONS — Ambiguities that could flip test results if wrong (case sensitivity,
   null vs empty string, priority order, OR vs AND logic, etc.)
3. MODULE LIST — The logical layers to test. Each condition branch = one module.
   Always include:
     - One module per routing/branching condition in the requirement
     - A "Priority & Fallthrough" module (cross-condition interaction tests)
     - A "Data Integrity" module (no message loss, no mutation, no spillover,
       offset commit / pod restart behaviour for Kafka/streaming systems)

Return ONLY this JSON — no other text:

{{
  "app_context": {{
    "app_name": "<system name>",
    "system_type": "<e.g. Kafka Streams App>",
    "routing_summary": "<one clear paragraph describing the full routing logic>",
    "input_fields": ["<field1>", "<field2>"],
    "output_destinations": ["<dest1>", "<dest2>"]
  }},
  "assumptions": [
    {{
      "id": "A1",
      "assumption": "<what is assumed>",
      "impact_if_wrong": "<which tests or decisions would change>"
    }}
  ],
  "modules": [
    {{
      "module_id": "M1",
      "module_name": "<name>",
      "description": "<what this module tests and why it matters>",
      "min_test_cases": <integer — recommended minimum for good coverage>
    }}
  ],
  "execution_order": [
    {{
      "order": 1,
      "module_id": "M1",
      "reason": "<why this module should run first>"
    }}
  ]
}}
"""


def call_plan(requirement: str) -> dict:
    print("  📋 Call 1/N: Generating test plan (context + assumptions + modules)...")
    prompt = PROMPT_PLAN.format(requirement=requirement)
    raw    = call_claude(prompt, max_tokens=2048)
    result = parse_json(raw, label="plan")
    mods   = result.get("modules", [])
    print(f"  ✅ Plan ready: {len(mods)} modules identified, {len(result.get('assumptions', []))} assumptions surfaced.\n")
    return result


# ==================================================================
# SECTION 5: CALL 2..N — TEST CASES PER MODULE
#
# One focused API call per module.
# Each call only knows about its own module — never the full list.
# This keeps responses small and prevents truncation entirely.
# ==================================================================

PROMPT_MODULE = """
You are a Principal QA Engineer. You are writing test cases for ONE specific module
of a larger test suite.

============================
SYSTEM UNDER TEST:
============================
{app_context}

FULL ROUTING LOGIC:
{routing_summary}

INPUT FIELDS: {input_fields}
OUTPUT DESTINATIONS: {output_destinations}

CONFIRMED ASSUMPTIONS:
{assumptions}
============================

============================
YOUR MODULE: {module_name}
DESCRIPTION: {module_description}
============================

Write at least {min_test_cases} test cases for THIS MODULE ONLY.

Rules:
- Declare ALL input fields in every test case (null or specific value) — never leave
  any field ambiguous. The reader must know the COMPLETE system state.
- Use REALISTIC domain-appropriate test data (not placeholders):
    * IBANs: "IN12345678901234", "CH5600000001234567890", "GB29NWBK60161331926819",
             "DE89370400440532013000", "US64SVBKUS6S3300958879"
    * BICs follow SWIFT format: 4-char bank + 2-char country + 2-char location
             e.g. "ABCDINXX" (IN at pos 5-6), "DEUTCHFF", "HBUKGB4B"
    * For Priority/Fallthrough: always show what OTHER fields are set alongside
- For edge cases add a verify_note (what to confirm with dev before running)
- TC IDs must start from {tc_start_id} and increment (TC{tc_start_id:03d}, TC{tc_next_id:03d}, ...)
- Cover for this module: positive matches, negative matches, boundary values,
  wrong-position matches, empty string vs null, case sensitivity, single-char values,
  both debtor AND creditor sides triggering independently

Return ONLY this JSON — no other text:

{{
  "module_id": "{module_id}",
  "module_name": "{module_name}",
  "test_cases": [
    {{
      "id": "TC{tc_start_id:03d}",
      "type": "positive",
      "title": "<clear specific title>",
      "input_state": {{
        "<field1>": "<value or null>",
        "<field2>": "<value or null>"
      }},
      "expected_output": "<exact routing destination and outcome>",
      "verify_note": "<string or null>"
    }}
  ]
}}
"""


def call_module(
    module: dict,
    plan: dict,
    tc_start: int
) -> dict:
    """
    Generates test cases for a single module.
    tc_start = the TC ID number to start from (so IDs are globally unique).
    """
    ctx          = plan["app_context"]
    assumptions  = plan.get("assumptions", [])
    module_id    = module["module_id"]
    module_name  = module["module_name"]
    min_tcs      = module.get("min_test_cases", 8)
    tc_next      = tc_start + 1

    # Format assumptions as a clean list for the prompt
    assumption_text = "\n".join(
        f"  {a['id']}: {a['assumption']}" for a in assumptions
    )

    prompt = PROMPT_MODULE.format(
        app_context       = ctx.get("app_name", "") + " — " + ctx.get("system_type", ""),
        routing_summary   = ctx.get("routing_summary", ""),
        input_fields      = ", ".join(ctx.get("input_fields", [])),
        output_destinations = ", ".join(ctx.get("output_destinations", [])),
        assumptions       = assumption_text,
        module_name       = module_name,
        module_description= module.get("description", ""),
        min_test_cases    = min_tcs,
        tc_start_id       = tc_start,
        tc_next_id        = tc_next,
        module_id         = module_id,
    )

    print(f"  ⚙️  Generating test cases for: {module_id} — {module_name}...")
    raw    = call_claude(prompt, max_tokens=3000)
    result = parse_json(raw, label=module_id)
    count  = len(result.get("test_cases", []))
    print(f"  ✅ {count} test cases generated for {module_id}.\n")
    return result


# ==================================================================
# SECTION 6: MARKDOWN REPORT GENERATOR
# ==================================================================

def generate_markdown_report(plan: dict, modules_data: list, source_file: str) -> str:
    now  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ctx  = plan.get("app_context", {})
    assumptions = plan.get("assumptions", [])
    exec_order  = plan.get("execution_order", [])

    total = sum(len(m.get("test_cases", [])) for m in modules_data)

    # ── Header ──────────────────────────────────────────────────────
    md  = f"# 🧪 Functional Test Cases — {ctx.get('app_name', 'System Under Test')}\n\n"
    md += "```\n"
    md += f"{'='*72}\n"
    md += f"  FUNCTIONAL TEST CASES — {ctx.get('app_name', '').upper()}\n"
    md += f"{'='*72}\n"
    md += f"App Type   : {ctx.get('system_type', 'N/A')}\n"
    md += f"Generated  : {now}\n"
    md += f"Source File: {os.path.basename(source_file)}\n"
    md += f"Total TCs  : {total}\n"
    md += f"{'='*72}\n"
    routing = ctx.get("routing_summary", "")
    md += "Routing Summary:\n"
    for sentence in routing.replace(". ", ".\n").split("\n"):
        if sentence.strip():
            md += f"  {sentence.strip()}\n"
    md += "\nOutput Destinations:\n"
    for dest in ctx.get("output_destinations", []):
        md += f"  → {dest}\n"
    md += f"{'='*72}\n"
    md += "```\n\n"

    # ── Assumption Log ───────────────────────────────────────────────
    md += "---\n\n"
    md += "## 📌 Assumption Log\n\n"
    md += "> ⚠️ Confirm ALL assumptions with Dev / BA **before** test execution.\n\n"
    md += "```\n"
    for a in assumptions:
        md += f"{a.get('id', '')} | {a.get('assumption', '')}\n"
        md += f"   | Impact if wrong: {a.get('impact_if_wrong', '')}\n\n"
    md += "```\n\n"

    # ── Test Cases by Module ─────────────────────────────────────────
    for mod in modules_data:
        mod_id   = mod.get("module_id", "")
        mod_name = mod.get("module_name", "")
        tcs      = mod.get("test_cases", [])

        md += "---\n\n"
        md += f"## {mod_id}: {mod_name}\n\n"
        md += "```\n"

        for tc in tcs:
            tc_id       = tc.get("id", "N/A")
            tc_title    = tc.get("title", "N/A")
            tc_type     = tc.get("type", "").upper()
            input_state = tc.get("input_state", {})
            expected    = tc.get("expected_output", "N/A")
            verify      = tc.get("verify_note")

            tag = {
                "POSITIVE":  "      ",
                "NEGATIVE":  "[NEG] ",
                "EDGE_CASE": "[EDG] ",
                "SECURITY":  "[SEC] ",
            }.get(tc_type, "      ")

            md += f"{tc_id} {tag}| {tc_title}\n"
            md += f"        Input  :\n"
            for field, value in input_state.items():
                display = f'"{value}"' if value not in [None, "null", "NULL", ""] else "null"
                md += f"                  {field} = {display}\n"
            md += f"        Expect : {expected}\n"
            if verify and verify not in [None, "null", "NULL", ""]:
                md += f"                  VERIFY: {verify}\n"
            md += "\n"

        md += "```\n\n"

    # ── Suggested Execution Order ────────────────────────────────────
    md += "---\n\n"
    md += "## 🎯 Suggested Execution Order\n\n"
    md += "```\n"
    for item in exec_order:
        md += f"  {item.get('order', '')}. {item.get('module_id', '')} — {item.get('reason', '')}\n"
    md += "```\n\n"

    # ── Quick Reference ──────────────────────────────────────────────
    md += "---\n\n"
    md += "## 📊 Quick Reference — Test Case Count by Module\n\n"
    md += "```\n"
    for mod in modules_data:
        mod_name = mod.get("module_name", "")
        tcs      = mod.get("test_cases", [])
        if tcs:
            first = tcs[0].get("id", "")
            last  = tcs[-1].get("id", "")
            md += f"  {mod.get('module_id', ''):<4} — {mod_name:<45}: {first} – {last}  ({len(tcs)} TCs)\n"
    md += f"  {'─'*72}\n"
    md += f"  {'TOTAL':<52}: {total} test cases\n"
    md += "```\n\n"
    md += "---\n\n"
    md += f"*Generated by STLC Agentic Framework — Agent 1 v4.0 | {now}*\n"

    return md


# ==================================================================
# SECTION 7: FILE SAVERS
# ==================================================================

def save_markdown(content: str, filename: str):
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  📄 Markdown report    : {filename}")

def save_json(data: dict, filename: str):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"  💾 JSON (Agent 2 input): {filename}")


# ==================================================================
# SECTION 8: MAIN PIPELINE
# ==================================================================

def run_agent1(filepath: str) -> dict:
    print("\n" + "="*60)
    print("🤖 AGENT 1: Requirement Analyzer  |  v4.0")
    print("="*60 + "\n")

    # ── Step 1: Read file ────────────────────────────────────────────
    requirement_text = read_requirement_file(filepath)

    # ── Step 2: Call 1 — Plan (context + assumptions + module list) ──
    print("🗺️  PHASE 1: Building test plan...\n")
    plan = call_plan(requirement_text)

    # ── Step 3: Call per module — test cases ─────────────────────────
    print("⚙️  PHASE 2: Generating test cases module by module...\n")
    modules_data = []
    tc_counter   = 1   # Global TC ID counter — ensures unique IDs across modules

    for i, module in enumerate(plan.get("modules", [])):
        result      = call_module(module, plan, tc_start=tc_counter)
        generated   = result.get("test_cases", [])
        tc_counter += len(generated)
        # Merge module metadata back in
        result["module_id"]   = module["module_id"]
        result["module_name"] = module["module_name"]
        modules_data.append(result)

    # ── Step 4: Assemble and save ─────────────────────────────────────
    total = sum(len(m.get("test_cases", [])) for m in modules_data)
    print(f"✅ PHASE 2 complete: {total} total test cases across {len(modules_data)} modules.\n")

    print("📝 PHASE 3: Generating outputs...\n")
    md_report = generate_markdown_report(plan, modules_data, filepath)
    base_name = os.path.splitext(os.path.basename(filepath))[0]

    # Full assembled JSON (for downstream agents)
    full_output = {
        "app_context":     plan.get("app_context", {}),
        "assumptions":     plan.get("assumptions", []),
        "execution_order": plan.get("execution_order", []),
        "modules":         modules_data,
        "summary": {
            "total_test_cases": total,
            "module_count":     len(modules_data),
            "generated_at":     datetime.now().isoformat()
        }
    }

    save_markdown(md_report, filename=f"{base_name}_test_cases.md")
    save_json(full_output,   filename=f"{base_name}_test_cases.json")

    print("\n" + "="*60)
    print(f"✅ AGENT 1 COMPLETE — {total} test cases generated")
    print("="*60)

    return full_output


# ==================================================================
# ENTRY POINT
# ==================================================================

if __name__ == "__main__":
    print("\n" + "="*60)
    print(" STLC Agentic Framework — Agent 1  |  v4.0")
    print(" Supported input: .txt  |  .pdf  |  .docx")
    print("="*60 + "\n")

    filepath = input("📂 Enter path to your requirement file: ").strip()
    run_agent1(filepath)
