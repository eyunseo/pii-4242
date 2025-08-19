from flask import Blueprint, request, jsonify, render_template, abort
import os, json
from dotenv import load_dotenv

report_bp = Blueprint("report", __name__)
load_dotenv()  # .env 로드

def _mask_like(v: str) -> str:
    if not v: return ""
    return "".join("●" if ch.isdigit() else ("★" if ch.isalpha() else ch) for ch in v)

def _validate_report(obj):
    base = {"summary": "", "overall_risk": "low", "findings": []}
    if not isinstance(obj, dict): return base
    out = {}
    out["summary"] = str(obj.get("summary", ""))[:2000]
    risk = str(obj.get("overall_risk", "low")).lower()
    out["overall_risk"] = risk if risk in ("low","medium","high","critical") else "low"
    finds = obj.get("findings", [])
    norm = []
    if isinstance(finds, list):
        for f in finds[:100]:
            if not isinstance(f, dict): continue
            pii_type = str(f.get("pii_type", "") or "")[:64]
            try: count = int(f.get("count", 0))
            except: count = 0
            impact = str(f.get("impact", "") or "")[:600]
            lh = str(f.get("likelihood", "low")).lower()
            if lh not in ("low","medium","high"): lh = "low"
            rec = str(f.get("recommendation", "") or "")[:600]
            if pii_type:
                norm.append({"pii_type":pii_type,"count":count,"impact":impact,"likelihood":lh,"recommendation":rec})
    out["findings"] = norm
    return out

@report_bp.route("/preview", methods=["GET","POST"])
def preview():
    if request.method == "GET":
        # 빈값으로도 페이지 열리게
        return render_template("report.html",
            type_count=0, types=[], original="", redacted="")

    # ---- POST (폼) ----
    original = (request.form.get("original_text") or "")[:10000]
    redacted = (request.form.get("redacted_text") or "")[:10000]
    raw_types = request.form.get("types") or "[]"
    try:
        types = json.loads(raw_types) if raw_types.strip().startswith("[") \
                else [t.strip() for t in raw_types.split(",") if t.strip()]
    except Exception:
        types = []

    if not (original or redacted):
        return abort(400, "original_text or redacted_text required")

    return render_template("report.html",
        type_count=len(types), types=types,
        original=original, redacted=redacted)

@report_bp.route("/gpt", methods=["POST","OPTIONS"])
def gpt_report():
    # CORS preflight
    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json(silent=True) or {}
    try: pii_count = int(data.get("piiCount") or 0)
    except: pii_count = 0
    by_type  = data.get("byType") or {}
    examples = data.get("examples") or {}

    # 혹시 원문이 섞여오면 즉석 마스킹
    safe_examples = {}
    if isinstance(examples, dict):
        for k, arr in examples.items():
            vals = []
            if isinstance(arr, list):
                for v in arr[:2]:
                    vals.append(_mask_like(str(v)))
            safe_examples[str(k)] = vals

    # Gemini 호출
    try:
        import google.generativeai as genai
        api_key = os.getenv("GOOGLE_API_KEY", "").strip()
        if not api_key:
            return jsonify({"ok": False, "error": "GOOGLE_API_KEY not set"}), 500
        genai.configure(api_key=api_key)

        sys_prompt = (
            "역할: 당신은 개인정보보호 리스크 분석가다.\n"
            "- 제공된 '비식별 탐지 요약'만 근거로 평가한다.\n"
            "- 출력은 반드시 JSON(RiskReport 스키마)로만 반환한다(텍스트 문단 금지).\n"
            "- 한국어로 간결하고 실행가능하게 작성한다.\n"
            "RiskReport:\n"
            "{ summary:str, overall_risk:('low'|'medium'|'high'|'critical'), "
            "  findings:[{pii_type:str,count:int,impact:str,likelihood:('low'|'medium'|'high'),recommendation:str}] }"
        )
        user_prompt = (
            f"[탐지 요약]\n"
            f"- 총 PII 건수: {pii_count}\n"
            f"- 유형별 건수: {json.dumps(by_type, ensure_ascii=False)}\n"
            f"- 샘플(마스킹본, 각 최대 2개): {json.dumps(safe_examples, ensure_ascii=False)}\n"
            "위 스키마에 맞는 JSON만 출력해줘."
        )

        model = genai.GenerativeModel("gemini-1.5-flash")
        generation_config = {"temperature": 0, "response_mime_type": "application/json"}
        resp = model.generate_content(
            [{"role":"user","parts":[sys_prompt+"\n\n"+user_prompt]}],
            generation_config=generation_config,
        )

        # 응답 파싱
        text = ""
        try:
            text = resp.text or ""
        except Exception:
            try:
                text = resp.candidates[0].content.parts[0].text
            except Exception:
                text = ""
        try:
            raw = json.loads(text)
        except Exception:
            s = text.strip()
            if s.startswith("```"):
                s = s.strip("`")
                if "\n" in s: s = s.split("\n",1)[1]
            try: raw = json.loads(s)
            except Exception: raw = {}

        report = _validate_report(raw)
        return jsonify({"ok": True, "report": report})

    except Exception as e:
        # 실패 시 안전 폴백
        fallback = {
            "summary": "자동 분석에 실패했습니다. 유형별 건수와 샘플을 확인하여 수동 점검이 필요합니다.",
            "overall_risk": "medium" if pii_count>0 else "low",
            "findings": [
                {"pii_type": k, "count": int(v or 0), "impact": "유출 시 악용 가능성", "likelihood":"medium", "recommendation":"전면 마스킹 및 저장 제한"}
                for k, v in (by_type.items() if isinstance(by_type, dict) else [])
            ]
        }
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}", "report": _validate_report(fallback)}), 200
