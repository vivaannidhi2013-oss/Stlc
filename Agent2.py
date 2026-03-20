"""
=============================================================
 STLC Agentic Framework — Agent 2: Dev Config Generator
 VERSION 2.0 — STANDALONE
=============================================================
 - Reads requirment.txt from the same folder automatically
 - No dependency on Agent 1 or any other agent
 - Generates developer-ready HOCON/JSON routing config

 Output files (saved in the same folder):
   requirment_routing_config.json          ← clean, use directly
   requirment_routing_config_annotated.json ← with inline comments
   requirment_routing_config.conf          ← HOCON format
   requirment_routing_config_report.md     ← human-readable summary

 Install: pip install boto3
 Run:     python agent2_dev_config_generator.py
=============================================================
"""

import boto3
import json
import os
import re
import time
from datetime import datetime
from botocore.exceptions import ClientError


# ------------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------------
AWS_REGION  = "eu-west-1"
MODEL_ID    = "eu.anthropic.claude-3-5-sonnet-20240620-v1:0"
MAX_RETRIES = 3
RETRY_DELAY = 5

# Auto-resolved paths — requirement file must be in the same folder as this script
SCRIPT_DIR       = os.path.dirname(os.path.abspath(__file__))
REQUIREMENT_FILE = os.path.join(SCRIPT_DIR, "requirment.txt")
OUTPUT_BASE      = os.path.join(SCRIPT_DIR, "requirment")


# ------------------------------------------------------------------
# BEDROCK CLIENT
# ------------------------------------------------------------------
client = boto3.client("bedrock-runtime", region_name=AWS_REGION)


# ==================================================================
# SECTION 1: REQUIREMENT READER
# ==================================================================

def read_requirement() -> str:
    if not os.path.exists(REQUIREMENT_FILE):
        raise FileNotFoundError(
            f"requirment.txt not found in: {SCRIPT_DIR}\n"
            f"Make sure the file is in the same folder as this script."
        )
    with open(REQUIREMENT_FILE, "r", encoding="utf-8") as f:
        content = f.read().strip()
    print(f"  ✅ Read requirment.txt ({len(content)} characters)\n")
    return content


# ==================================================================
# SECTION 2: BEDROCK CALLER
# ==================================================================

def call_claude(prompt: str, max_tokens: int = 2048) -> str:
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
            print(f"  ⚠️  Attempt {attempt}/{MAX_RETRIES} failed: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
            else:
                raise


# ==================================================================
# SECTION 3: JSON PARSER
# ==================================================================

def parse_json(raw: str, label: str = "") -> dict:
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

    # Fix trailing commas
    text = re.sub(r",\s*(\}|\])", r"\1", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        debug_file = os.path.join(SCRIPT_DIR, f"debug_agent2_{label}.txt")
        with open(debug_file, "w", encoding="utf-8") as f:
            f.write(raw)
        print(f"\n  ❌ JSON parse failed for '{label}': {e}")
        print(f"  Raw response saved to: {debug_file}")
        raise


# ==================================================================
# SECTION 4: CALL 1 — LOGIC EXTRACTION
# Understands the routing logic as structured data.
# Does NOT generate any config yet.
# ==================================================================

PROMPT_EXTRACT_LOGIC = """
You are a senior software architect specialising in Kafka Streams and event-driven systems.

Read the following requirement and extract the routing logic as structured data.
Do NOT generate any config file yet — only extract and structure the logic.

============================
REQUIREMENT:
============================
{requirement}
============================

The routing config format you will generate later follows this pattern:
  - Rules are evaluated top-to-bottom, first match wins
  - Each rule has a condition (JSONPath-style) and a targetTopic
  - Conditions use: transactionDtoList[*].<fieldName>.substring(startIdx, endIdx) == 'VALUE'
  - The last rule is always a catch-all: condition "true" → default topic
  - Multiple fields can trigger the same topic (write one rule per field, OR logic)

Extract and return ONLY this JSON — no other text:

{{
  "system_name": "<name of the system being configured>",
  "input_collection": "transactionDtoList",
  "routing_conditions": [
    {{
      "priority": 1,
      "description": "<what this condition checks>",
      "field_checks": [
        {{
          "field_path": "<exact DTO field name, e.g. debtorIban>",
          "substring_start": <integer or null>,
          "substring_end": <integer or null>,
          "match_values": ["<value1>", "<value2>"],
          "null_guard_required": <true or false>
        }}
      ],
      "target_topic": "<kafka topic name for matching messages>"
    }}
  ],
  "default_rule": {{
    "description": "All non-matching transactions",
    "target_topic": "<fallback topic name>"
  }}
}}
"""


def call_extract_logic(requirement: str) -> dict:
    print("  🔍 Call 1/2: Extracting routing logic...")
    raw    = call_claude(PROMPT_EXTRACT_LOGIC.format(requirement=requirement), max_tokens=2048)
    result = parse_json(raw, label="logic_extraction")
    print(f"  ✅ {len(result.get('routing_conditions', []))} routing condition(s) extracted.\n")
    return result


# ==================================================================
# SECTION 5: CALL 2 — CONFIG GENERATION
# Takes the structured logic and writes the actual config.
# ==================================================================

PROMPT_GENERATE_CONFIG = """
You are a senior Kafka Streams developer. Generate a routing configuration file
from the structured routing logic below.

============================
ROUTING LOGIC:
============================
{logic_json}
============================

REFERENCE FORMAT — match this structure exactly:
{{
  "type": "routing",
  "rules": [
    {{
      "condition": "transactionDtoList[*].debtorIban.substring(0, 2) == 'IN'",
      "targetTopic": "restricted-onprem"
    }},
    {{
      "condition": "true",
      "targetTopic": "compliant-cloud"
    }}
  ]
}}

RULES:
1. One rule per field check — do not combine multiple fields into one condition
2. Order rules by priority (as given in the logic) — first match wins
3. If a field requires a null guard, add it as a separate preceding rule or
   handle it with a combined expression using != null &&
4. The very last rule must always be: {{"condition": "true", "targetTopic": "<default>"}}
5. Use exact field names from the logic (preserve camelCase)
6. Match values must use single quotes inside the condition string: == 'IN'

Also generate:
- A HOCON .conf version of the same config
- A human-readable summary

Return ONLY this JSON — no other text:

{{
  "routing_config": {{
    "type": "routing",
    "rules": [
      {{
        "condition": "<expression>",
        "targetTopic": "<topic>",
        "_comment": "<one line explanation for developers>"
      }}
    ]
  }},
  "hocon_text": "<HOCON .conf content as a string — use \\n for newlines>",
  "summary": {{
    "total_rules": <integer>,
    "fields_covered": ["<field1>", "<field2>"],
    "default_topic": "<fallback topic>",
    "notes": "<any important notes for developers>"
  }}
}}
"""


def call_generate_config(logic: dict) -> dict:
    print("  ⚙️  Call 2/2: Generating routing config...")
    raw    = call_claude(
        PROMPT_GENERATE_CONFIG.format(logic_json=json.dumps(logic, indent=2)),
        max_tokens=3000
    )
    result = parse_json(raw, label="config_generation")
    rules  = result.get("routing_config", {}).get("rules", [])
    print(f"  ✅ {len(rules)} rules generated (including catch-all).\n")
    return result


# ==================================================================
# SECTION 6: OUTPUT WRITERS
# ==================================================================

def write_outputs(logic: dict, result: dict):
    now    = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    config = result.get("routing_config", {})
    rules  = config.get("rules", [])
    summary = result.get("summary", {})

    # ── Clean JSON (no comments) ─────────────────────────────────────
    clean_config = {
        "type":  config.get("type", "routing"),
        "rules": [
            {"condition": r["condition"], "targetTopic": r["targetTopic"]}
            for r in rules
        ]
    }
    clean_path = f"{OUTPUT_BASE}_routing_config.json"
    with open(clean_path, "w", encoding="utf-8") as f:
        json.dump(clean_config, f, indent=2)
    print(f"  📄 Clean JSON           : {os.path.basename(clean_path)}")

    # ── Annotated JSON (with _comment fields) ────────────────────────
    annotated_path = f"{OUTPUT_BASE}_routing_config_annotated.json"
    with open(annotated_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    print(f"  📄 Annotated JSON       : {os.path.basename(annotated_path)}")

    # ── HOCON .conf ──────────────────────────────────────────────────
    hocon_text = result.get("hocon_text", "")
    hocon_text = hocon_text.replace("\\n", "\n").replace("\\t", "  ")
    hocon_path = f"{OUTPUT_BASE}_routing_config.conf"
    with open(hocon_path, "w", encoding="utf-8") as f:
        f.write(hocon_text)
    print(f"  📄 HOCON .conf          : {os.path.basename(hocon_path)}")

    # ── Markdown summary report ──────────────────────────────────────
    md  = f"# ⚙️ Routing Config — Generation Report\n\n"
    md += f"| | |\n|---|---|\n"
    md += f"| **System** | {logic.get('system_name', 'N/A')} |\n"
    md += f"| **Generated** | {now} |\n"
    md += f"| **Source** | `requirment.txt` |\n"
    md += f"| **Total Rules** | {summary.get('total_rules', len(rules))} |\n"
    md += f"| **Default Topic** | `{summary.get('default_topic', 'N/A')}` |\n"
    md += f"| **Fields Covered** | {', '.join(f'`{f}`' for f in summary.get('fields_covered', []))} |\n\n"
    md += "---\n\n## 📋 Rules\n\n"
    md += "> Rules are evaluated **top to bottom — first match wins.**\n\n"
    md += "| # | Condition | Target Topic | Note |\n|---|---|---|---|\n"
    for i, rule in enumerate(rules, 1):
        cond    = f"`{rule.get('condition', '')}`"
        topic   = f"`{rule.get('targetTopic', '')}`"
        comment = rule.get("_comment", "—")
        md += f"| {i} | {cond} | {topic} | {comment} |\n"
    if summary.get("notes"):
        md += f"\n---\n\n## 📝 Dev Notes\n\n> {summary['notes']}\n"
    md += f"\n---\n\n*Generated by STLC Agentic Framework — Agent 2 v2.0 | {now}*\n"

    report_path = f"{OUTPUT_BASE}_routing_config_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"  📄 Summary report       : {os.path.basename(report_path)}")


# ==================================================================
# SECTION 7: MAIN PIPELINE
# ==================================================================

def run_agent2():
    print("\n" + "="*60)
    print("⚙️  AGENT 2: Dev Config Generator  |  v2.0")
    print("="*60 + "\n")

    print(f"📂 Reading requirment.txt from: {SCRIPT_DIR}\n")
    requirement = read_requirement()

    print("🧠 PHASE 1: Understanding routing logic...\n")
    logic = call_extract_logic(requirement)

    print("🏗️  PHASE 2: Generating routing config...\n")
    result = call_generate_config(logic)

    print("💾 PHASE 3: Writing output files...\n")
    write_outputs(logic, result)

    rules_count = len(result.get("routing_config", {}).get("rules", []))
    print("\n" + "="*60)
    print(f"✅ AGENT 2 COMPLETE — {rules_count} rules written")
    print("="*60)


# ==================================================================
# ENTRY POINT
# ==================================================================

if __name__ == "__main__":
    run_agent2()
