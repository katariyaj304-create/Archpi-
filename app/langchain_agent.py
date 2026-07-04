"""
ArchPi LangChain Agent
Powered by HuggingFace Router (DeepSeek) with Gemini fallback for
Intelligent Architectural Analysis.
Extracts deep, research-paper-grade structured data from research text.
"""
import os
import json
import re
import sys
import traceback
import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# ==============================================================
# CONFIGURATION
# ==============================================================
HF_TOKEN = os.environ.get("HUGGINGFACEHUB_API_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

HF_ROUTER_URL = "https://router.huggingface.co/v1/chat/completions"
# Only models actually served through the HF router on this account.
# DeepSeek-V3.2 is the one confirmed to respond; keep others as long-shots.
HF_MODELS = [
    "deepseek-ai/DeepSeek-V3.2",
]
GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]

active_model = None  # last model that successfully answered


# ==============================================================
# LLM CALL LAYER — HF router first, Gemini fallback
# ==============================================================
def _call_hf(model, messages, max_tokens, temperature):
    res = requests.post(
        HF_ROUTER_URL,
        headers={
            "Authorization": f"Bearer {HF_TOKEN}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        },
        # short deadline on purpose: when the HF router is overloaded it hangs
        # for minutes before returning a 504 — fail fast so the Gemini
        # fallback keeps total research time predictable
        timeout=100,
    )
    if res.status_code != 200:
        raise RuntimeError(f"HTTP {res.status_code}: {res.text[:150]}")
    data = res.json()
    text = data["choices"][0]["message"]["content"] or ""
    # Strip any <think> reasoning blocks
    text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
    if not text:
        raise RuntimeError("empty response")
    return text


def _call_gemini(model, messages, max_tokens, temperature):
    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    user_parts = [m["content"] for m in messages if m["role"] != "system"]
    body = {
        "contents": [{"role": "user", "parts": [{"text": "\n\n".join(user_parts)}]}],
        "generationConfig": {
            # Gemini 2.5 Flash supports very large outputs; give ample headroom so
            # long research-paper JSON is never truncated mid-object.
            "maxOutputTokens": max(max_tokens, 32768),
            "temperature": temperature,
            # Disable hidden reasoning: its tokens count against maxOutputTokens
            # and silently truncate long JSON (same fix as /api/analyze-photo).
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    if system_parts:
        body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
    res = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
        json=body,
        timeout=320,
    )
    if res.status_code != 200:
        raise RuntimeError(f"HTTP {res.status_code}: {res.text[:150]}")
    data = res.json()
    cand = (data.get("candidates") or [{}])[0]
    finish = cand.get("finishReason", "")
    if finish and finish != "STOP":
        print(f"  [Gemini] {model} finishReason={finish}")
    parts = cand.get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise RuntimeError(f"empty response (finishReason={finish or 'none'})")
    return text


def call_llm(messages, max_tokens=8192, temperature=0.2):
    """Call the best available LLM. Returns (text, model_name)."""
    global active_model
    errors = []
    if HF_TOKEN:
        for model in HF_MODELS:
            try:
                text = _call_hf(model, messages, max_tokens, temperature)
                active_model = model
                return text, model
            except Exception as e:
                print(f"  [LLM] {model} failed: {str(e)[:150]} -> trying next provider")
                errors.append(f"{model}: {str(e)[:120]}")
    if GEMINI_API_KEY:
        for model in GEMINI_MODELS:
            try:
                text = _call_gemini(model, messages, max_tokens, temperature)
                active_model = model
                return text, model
            except Exception as e:
                print(f"  [LLM] {model} failed: {str(e)[:150]} -> trying next provider")
                errors.append(f"{model}: {str(e)[:120]}")
    raise RuntimeError("All LLM providers failed: " + " | ".join(errors))


def initialize_model():
    """Ping providers so /status reports the live model at startup."""
    try:
        text, model = call_llm(
            [{"role": "user", "content": "Respond with only the word: OK"}],
            max_tokens=10, temperature=0.1,
        )
        print(f"  [OK] Model ready: {model} (test: {text.strip()[:30]})")
        return True
    except Exception as e:
        print(f"  [WARN] No LLM available: {str(e)[:200]}")
        return False


# ==============================================================
# EXTRACTION PROMPT — deep research paper output
# ==============================================================
SYSTEM_PROMPT = """You are an expert architectural historian and structural engineering analyst
writing sections of an academic research paper. You extract and synthesize structured data
from research text combined with your own expert knowledge, and return ONLY valid JSON.
Never include markdown fences, explanations, or text outside the JSON object.
Always return complete, well-formed JSON. Write LONG, information-dense academic prose
inside the JSON string fields — never one-line summaries."""

def build_user_prompt(building_name, research_text):
    return f"""/no_think
Analyze the research text about "{building_name}", combine it with your own expert knowledge,
and produce a DEEP RESEARCH PAPER as a JSON object with this exact structure:

{{
  "height": "numeric height value or null",
  "heightUnit": "meters or feet or null",
  "constructionStart": "start year as string or null",
  "constructionEnd": "completion year as string or null",
  "cost": "cost description or null",
  "workers": "worker count string or null",
  "architect": "architect or engineer name or null",
  "floors": "floor count string or null",
  "overview": "3-5 LONG paragraphs (at least 400 words total) giving a research-paper style historical and architectural overview: commissioning context, political/cultural motivation, site selection, significance then and now. SEPARATE PARAGRAPHS WITH \\n\\n",
  "timelinePhases": [
    {{
      "year": "year string",
      "title": "phase title",
      "description": "2 LONG detailed paragraphs (at least 150 words) about what happened in this phase: methods, people, decisions, setbacks, milestones. SEPARATE WITH \\n\\n",
      "stats": [
        {{"label": "stat name", "value": "stat value"}},
        {{"label": "stat name", "value": "stat value"}},
        {{"label": "stat name", "value": "stat value"}},
        {{"label": "stat name", "value": "stat value"}}
      ]
    }}
  ],
  "materials": [
    {{
      "name": "material name",
      "quantity": "estimated quantity if known",
      "description": "2 detailed paragraphs (at least 120 words) explaining the material's structural role, why it was chosen, how it was worked, and its condition today. SEPARATE WITH \\n\\n",
      "category": "Structural or Foundation or Facade or Interior or Exterior or Decorative",
      "source": "where the material was sourced/quarried/manufactured, with transport details if known",
      "role": "one sentence: precise structural/architectural function",
      "properties": {{
        "density": "value with unit or best engineering estimate",
        "compressiveStrength": "value with unit or best engineering estimate",
        "tensileStrength": "value with unit or best engineering estimate",
        "durability": "short expert assessment",
        "thermalBehavior": "short expert assessment"
      }},
      "modernEquivalent": "what would be used today instead, one sentence"
    }}
  ],
  "designPhilosophy": "3-4 LONG paragraphs (at least 300 words) on architectural style, geometry, proportion systems, symbolism, precedents and influence on later architecture. SEPARATE WITH \\n\\n",
  "structuralSystem": "3-4 LONG paragraphs (at least 300 words) on the load path, foundation system, lateral system, construction techniques and engineering innovations, in academic tone. SEPARATE WITH \\n\\n",
  "engineeringChallenges": "3-4 LONG paragraphs (at least 300 words) detailing major engineering challenges, failures, risks and how each was overcome. SEPARATE WITH \\n\\n",
  "culturalImpact": "2-3 paragraphs (at least 200 words) on records, awards, cultural meaning, tourism, conservation status and restoration efforts. SEPARATE WITH \\n\\n",
  "features": ["feature1", "feature2", "feature3", "feature4", "feature5", "feature6"]
}}

REQUIREMENTS:
- Create 5-7 chronological timeline phases covering conception, planning, foundation, main construction, challenges, completion and later restorations.
- List EVERY construction material mentioned or known (aim for 4-8 materials) with full spec details.
- Where the research text lacks a number, use your own expert knowledge; only use null when truly unknown.
- Write like a published architecture research paper: specific dates, names, numbers, techniques — no filler sentences.

RESEARCH TEXT:
---
{research_text}
---

Return ONLY the JSON object, nothing else:"""


def repair_truncated_json(text):
    """Repair a JSON object that was cut off mid-generation (output token limit).
    Scans once tracking string/bracket state, remembers safe cut points, then
    tries closing the structure at each cut point from the end backwards."""
    start = text.find('{')
    if start == -1:
        return None
    s = re.sub(r'```\s*$', '', text[start:].rstrip()).rstrip()

    cut_points = []  # (index_after_char, stack_snapshot, is_comma)
    stack = []
    in_str = False
    esc = False
    for i, c in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c in '{[':
                stack.append(c)
            elif c in '}]':
                if stack:
                    stack.pop()
                cut_points.append((i + 1, tuple(stack), False))
            elif c == ',':
                cut_points.append((i, tuple(stack), True))

    # First attempt: repair at the very end (close open string, strip dangling
    # key/comma, close all open brackets)
    def try_candidate(chunk, open_stack, str_open):
        cand = chunk
        if str_open:
            cand += '"'
        cand = re.sub(r',\s*$', '', cand)
        cand = re.sub(r'"(?:[^"\\]|\\.)*"\s*:\s*$', '', cand)  # dangling "key":
        cand = re.sub(r',\s*$', '', cand)
        cand += ''.join('}' if b == '{' else ']' for b in reversed(open_stack))
        try:
            return json.loads(cand)
        except Exception:
            return None

    result = try_candidate(s, stack, in_str)
    if result is not None:
        return result

    # Walk back through the last safe cut points
    for idx, snap, _is_comma in reversed(cut_points[-120:]):
        result = try_candidate(s[:idx], list(snap), False)
        if result is not None:
            return result
    return None


def extract_json_from_text(text):
    """Extract a JSON object from model output that may contain extra text."""
    if not text:
        return None

    # Try direct parse
    try:
        return json.loads(text.strip())
    except Exception:
        pass

    # Try to find JSON block in markdown code fences
    code_block = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
    if code_block:
        try:
            return json.loads(code_block.group(1))
        except Exception:
            pass

    # Find outermost { ... } braces
    depth = 0
    start = -1
    for i, c in enumerate(text):
        if c == '{':
            if depth == 0:
                start = i
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0 and start != -1:
                candidate = text[start:i + 1]
                try:
                    return json.loads(candidate)
                except Exception:
                    # Try fixing trailing commas
                    try:
                        fixed = re.sub(r',\s*([}\]])', r'\1', candidate)
                        return json.loads(fixed)
                    except Exception:
                        start = -1

    # Last resort: the output was likely truncated mid-JSON — repair it
    repaired = repair_truncated_json(text)
    if repaired is not None:
        print("  [WARN] JSON was truncated; repaired successfully")
    return repaired


def validate_and_normalize(data):
    """Ensure the extracted data has all required fields with defaults."""
    defaults = {
        "height": None,
        "heightUnit": None,
        "constructionStart": None,
        "constructionEnd": None,
        "cost": None,
        "workers": None,
        "architect": None,
        "floors": None,
        "overview": "",
        "timelinePhases": [],
        "materials": [],
        "designPhilosophy": "",
        "structuralSystem": "",
        "engineeringChallenges": "",
        "culturalImpact": "",
        "features": []
    }
    for key, default in defaults.items():
        if key not in data or data[key] is None:
            data[key] = default

    # Normalize timeline phases
    for phase in data.get("timelinePhases", []):
        phase.setdefault("year", "")
        phase.setdefault("title", "Construction Phase")
        phase.setdefault("description", "")
        phase.setdefault("stats", [])

    # Normalize materials
    for mat in data.get("materials", []):
        mat.setdefault("name", "Building Material")
        mat.setdefault("quantity", "Various")
        mat.setdefault("description", "")
        mat.setdefault("category", "General")
        mat.setdefault("source", "")
        mat.setdefault("role", "")
        mat.setdefault("modernEquivalent", "")
        props = mat.get("properties") or {}
        for pk in ("density", "compressiveStrength", "tensileStrength", "durability", "thermalBehavior"):
            props.setdefault(pk, "")
        mat["properties"] = props

    return data


# ==============================================================
# API ENDPOINTS
# ==============================================================

@app.route('/extract', methods=['POST'])
def extract():
    """Main extraction endpoint. Receives research text, returns structured JSON."""
    try:
        data = request.json
        building_name = data.get('buildingName', '')
        research_text = data.get('researchText', '')

        if not research_text:
            return jsonify({"error": "No research text provided"}), 400

        # Truncate to stay within token limits
        max_chars = 14000
        if len(research_text) > max_chars:
            research_text = research_text[:max_chars]

        user_prompt = build_user_prompt(building_name, research_text)

        print(f"\n  Processing: {building_name} ({len(research_text)} chars)")

        raw_result, model_used = call_llm(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=8192,
            temperature=0.2,
        )
        print(f"  Model: {model_used}")
        print(f"  Raw response: {len(str(raw_result))} chars")

        # Parse JSON from model output
        parsed = extract_json_from_text(str(raw_result))

        # A parsed-but-gutted result (no phases, no materials) means the output
        # was cut short — retry once before accepting it
        if parsed and not parsed.get('timelinePhases') and not parsed.get('materials'):
            print("  [WARN] Extraction returned empty phases/materials — retrying once")
            try:
                raw_retry, model_retry = call_llm(
                    [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt}
                    ],
                    max_tokens=8192,
                    temperature=0.4,
                )
                parsed_retry = extract_json_from_text(str(raw_retry))
                if parsed_retry and (parsed_retry.get('timelinePhases') or parsed_retry.get('materials')):
                    parsed, model_used = parsed_retry, model_retry
                    print(f"  [OK] Retry produced a complete report via {model_retry}")
            except Exception as retry_err:
                print(f"  [WARN] Retry failed: {str(retry_err)[:120]}")

        if parsed:
            parsed = validate_and_normalize(parsed)
            parsed['modelUsed'] = model_used
            parsed['status'] = 'success'
            print(f"  [OK] Extraction successful: {len(parsed.get('timelinePhases', []))} phases, {len(parsed.get('materials', []))} materials")
            return jsonify(parsed)
        else:
            print(f"  [FAIL] Could not parse JSON from model output")
            print(f"  Raw (first 300 chars): {str(raw_result)[:300]}")
            return jsonify({
                "error": "Could not parse structured data from model output",
                "raw_preview": str(raw_result)[:500],
                "status": "parse_error"
            }), 422

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "error": str(e),
            "status": "error"
        }), 500



# ==============================================================
# KNOWLEDGE PROMPT — LLM shares its own built-in knowledge
# ==============================================================
KNOWLEDGE_SYSTEM = """You are an expert architectural historian and structural engineer
writing for an academic research dossier.
Share your deep knowledge about the requested building or structure.
Draw from your training data — historical facts, engineering details, cultural significance.
Be detailed, factual, and comprehensive. Include dates, dimensions, people, materials, and context.
If you are uncertain about a fact, say so. Never fabricate specific numbers you aren't confident about."""

def build_knowledge_prompt(building_name):
    return f"""/no_think
Share everything you know about "{building_name}" as an architectural and engineering expert.

Cover these aspects in depth, each as its own titled section with full paragraphs:
1. **Overview**: What is it? Where is it? Why is it significant?
2. **History**: When was it built? Who designed/built it? What was the motivation?
3. **Dimensions**: Height, width, floors, area — any measurements you know
4. **Materials**: Every material used, with quantities, sourcing, and properties
5. **Structural System**: The engineering approach (e.g., steel frame, reinforced concrete, buttressed core), load paths, foundations
6. **Construction Process**: How was it built? Key phases? How long did it take?
7. **Engineering Challenges**: What problems were faced? How were they solved?
8. **Architectural Style**: Design philosophy, geometry, proportion, symbolism
9. **Cultural Significance**: Awards, records, cultural impact, conservation
10. **Cost & Labor**: Construction cost, workforce size and organization

Write a comprehensive, detailed response of AT LEAST 1000 words. Be specific with facts,
dates, and numbers you are confident about — this text will be quoted in a research paper."""


# ==============================================================
# REASONING PROMPT — LLM analyzes and synthesizes findings
# ==============================================================
REASON_SYSTEM = """You are an expert analyst. You receive research findings from multiple internet sources about a building.
Your job is to:
1. Identify the most reliable and consistent facts across sources
2. Resolve any contradictions between sources
3. Add your own expert knowledge to fill gaps
4. Provide deeper insights that go beyond what the sources say
5. Highlight particularly interesting or surprising findings

Return your analysis as a well-structured text response. Be thorough and insightful."""

def build_reason_prompt(building_name, findings_text):
    return f"""/no_think
Analyze the following research findings about "{building_name}".

RESEARCH FINDINGS FROM INTERNET SOURCES:
---
{findings_text[:8000]}
---

Provide a deep analysis of AT LEAST 500 words covering:
1. **Verified Facts**: Key facts that appear consistently across sources
2. **Contradictions**: Any conflicting information you notice, and which is more likely correct
3. **Gap Analysis**: Important information that seems missing from the sources
4. **Expert Insights**: Your own expert knowledge that adds depth to these findings
5. **Significance Assessment**: What makes this structure architecturally or engineering-wise remarkable

Be detailed, analytical, and insightful. Add value beyond what the raw sources provide."""


@app.route('/knowledge', methods=['POST'])
def knowledge():
    """LLM shares its own built-in knowledge about a building."""
    try:
        data = request.json
        building_name = data.get('buildingName', '')

        if not building_name:
            return jsonify({"error": "No building name provided"}), 400

        print(f"\n  [KNOWLEDGE] Querying LLM knowledge for: {building_name}")

        result_text, model_used = call_llm(
            [
                {"role": "system", "content": KNOWLEDGE_SYSTEM},
                {"role": "user", "content": build_knowledge_prompt(building_name)}
            ],
            max_tokens=8192,
            temperature=0.3,
        )
        print(f"  [KNOWLEDGE] Got {len(result_text)} chars via {model_used}")

        return jsonify({
            "knowledge": result_text,
            "model": model_used,
            "status": "success"
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e), "status": "error"}), 500


@app.route('/reason', methods=['POST'])
def reason():
    """LLM analyzes and synthesizes research findings with its own reasoning."""
    try:
        data = request.json
        building_name = data.get('buildingName', '')
        findings = data.get('findings', '')

        if not building_name or not findings:
            return jsonify({"error": "buildingName and findings required"}), 400

        print(f"\n  [REASON] Reasoning about: {building_name} ({len(findings)} chars of findings)")

        result_text, model_used = call_llm(
            [
                {"role": "system", "content": REASON_SYSTEM},
                {"role": "user", "content": build_reason_prompt(building_name, findings)}
            ],
            max_tokens=8192,
            temperature=0.2,
        )
        print(f"  [REASON] Got {len(result_text)} chars via {model_used}")

        return jsonify({
            "analysis": result_text,
            "model": model_used,
            "status": "success"
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e), "status": "error"}), 500


@app.route('/status', methods=['GET'])
def status():
    """Health check endpoint. Returns model status."""
    available = bool(HF_TOKEN or GEMINI_API_KEY)
    return jsonify({
        "active": available,
        "model": active_model or (HF_MODELS[0] if HF_TOKEN else GEMINI_MODELS[0]),
        "preferredModel": HF_MODELS[0],
        "fallbackModels": GEMINI_MODELS,
        "status": "ready" if available else "unavailable",
        "token_configured": bool(HF_TOKEN),
        "gemini_configured": bool(GEMINI_API_KEY)
    })


# ==============================================================
# STARTUP
# ==============================================================
if __name__ == '__main__':
    print("=" * 60)
    print("  ArchPi LangChain Agent")
    print("  HuggingFace Router (DeepSeek) + Gemini fallback")
    print("=" * 60)
    print(f"  HF Token: {'configured' if HF_TOKEN else 'MISSING!'}")
    print(f"  Gemini Key: {'configured' if GEMINI_API_KEY else 'missing'}")
    print(f"  Preferred Model: {HF_MODELS[0]}")
    print("-" * 60)
    print("  Testing providers...")
    initialize_model()
    print("-" * 60)
    if active_model:
        print(f"  Active Model: {active_model}")
    else:
        print("  WARNING: No model responded. Endpoints will retry per-request.")
    print(f"  Server: http://127.0.0.1:5001")
    print("=" * 60)
    app.run(host='127.0.0.1', port=5001, debug=False)
