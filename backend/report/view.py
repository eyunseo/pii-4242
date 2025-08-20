from flask import Blueprint, request, jsonify, render_template, abort
import os, json, re
from dotenv import load_dotenv

report_bp = Blueprint("report", __name__)
load_dotenv()

RULES = {
    "주민등록번호":  {"impact": "1등급", "evidence": "6자리-7자리 구조와 생년월일 유효성 검사를 통과", "detector": "정규표현식+형식검증"},
    "외국인등록번호": {"impact": "1등급", "evidence": "6자리-7자리 구조와 7번째 자리 규칙 반영",           "detector": "정규표현식+형식검증"},
    "여권번호":     {"impact": "1등급", "evidence": "영문 2자 뒤 숫자 7자의 고정 패턴과 국가 코드 맥락",  "detector": "정규표현식"},
    "운전면허번호":  {"impact": "1등급", "evidence": "국내 운전면허번호 표준 형식과 구분자 패턴 일치",     "detector": "정규표현식"},
    "계좌번호":     {"impact": "1등급", "evidence": "은행계좌 길이 범위와 구분자·접두 규칙 일치",         "detector": "정규표현식"},
    "카드번호":     {"impact": "1등급", "evidence": "카드번호 길이와 Luhn 검사 통과",                     "detector": "정규표현식+검증"},
    "카드 유효기간": {"impact": "1등급", "evidence": "MM/YY(또는 YYYY) 형식과 유효 월·연도 범위 일치",    "detector": "정규표현식"},

    "이름":        {"impact": "2등급", "evidence": "한글 2~4자 인명 패턴과 호칭·문맥 결합",              "detector": "NER+규칙"},
    "생년월일":     {"impact": "2등급", "evidence": "YYYYMMDD/구분자 포함 날짜가 실제 달력에 존재",       "detector": "정규표현식+형식검증"},
    "성별":        {"impact": "2등급", "evidence": "성별 지시어·코드가 인근 문맥과 함께 등장",            "detector": "규칙"},
    "전화번호":     {"impact": "2등급", "evidence": "국번·길이·구분자 규칙(예: 010-XXXX-XXXX)과 일치",    "detector": "정규표현식"},
    "이메일":      {"impact": "2등급", "evidence": "local@domain 형태와 유효 TLD가 결합",                "detector": "정규표현식"},
    "주소":        {"impact": "2등급", "evidence": "도로명/행정구역 명칭과 번지·상세주소 패턴 결합",      "detector": "NER+규칙"},
    "연락처":      {"impact": "2등급", "evidence": "전화·이메일 등 연락 수단 표기가 문맥과 연결",         "detector": "규칙"},
}

def _mask_like(v: str) -> str:
    if not v: return ""
    return "".join("●" if ch.isdigit() else ("★" if ch.isalpha() else ch) for ch in v)

def _format_counts(by_type: dict):
    """'(이메일 1건, 전화번호 2건)', 총합 반환."""
    if not isinstance(by_type, dict):
        return "", 0
    items, total = [], 0
    for k, v in by_type.items():
        try:
            c = int(v or 0)
        except Exception:
            c = 0
        if c <= 0:
            continue
        total += c
        items.append((k, c))
    items.sort(key=lambda x: (-x[1], x[0]))
    if not items:
        return "", 0
    inside = ", ".join([f"{name} {cnt}건" for name, cnt in items])
    return f"({inside})", total

def _clean_sentence(s: str) -> str:
    if not s: return ""
    t = str(s)
    t = t.replace("유출될 경우", "유출되면")
    ban = [
        r"개인정보보호\s*정책을\s*준수.*?$",
        r"지속적인\s*모니터링.*?$",
        r"신속한\s*조치가\s*필요.*?$",
        r"개별적으로는\s*피해가\s*제한적.*?$",
    ]
    for bp in ban:
        t = re.sub(bp, "", t).strip()
    t = re.sub(r"\s{2,}", " ", t).strip().strip("., ")
    return t

def _validate_report(obj):
    """모델 응답 정규화 → 프론트에서 쓰는 키로 맞춤.
       overall_risk / reason 제거 버전."""
    base = {"summary": "", "combined_risk": "", "findings": []}
    if not isinstance(obj, dict):
        return base

    out = {}
    out["summary"] = _clean_sentence(obj.get("summary", ""))[:2000]
    out["combined_risk"] = _clean_sentence(obj.get("combined_risk", ""))[:2000]

    finds = obj.get("findings", []) or []
    norm = []
    for f in finds[:100]:
        if not isinstance(f, dict):
            continue
        pii_type = str(f.get("pii_type", "") or "")[:64]
        try:
            count = int(f.get("count", 0))
        except Exception:
            count = 0

        impact   = _clean_sentence(f.get("risk_explanation") or f.get("impact") or "")[:800]
        rec      = _clean_sentence(f.get("recommendation") or "")[:800]
        example  = _clean_sentence(f.get("example") or "")[:200]
        evidence = _clean_sentence(f.get("evidence") or "")[:400]
        # reason 제거

        if pii_type:
            norm.append({
                "pii_type": pii_type,
                "count": count,
                "impact": impact,
                "recommendation": rec,
                "example": example,
                "evidence": evidence,
            })
    out["findings"] = norm
    return out

@report_bp.route("/preview_gpt", methods=["GET", "POST"])
def preview_gpt():
    if request.method == "GET":
        return render_template(
            "newreport.html",
            type_count=0, types=[], original="", redacted="", answer=""
        )

    original = (request.form.get("original_text") or "")[:10000]
    redacted = (request.form.get("redacted_text") or "")[:10000]
    answer   = (request.form.get("answer_text")   or "")[:12000]
    raw_types = request.form.get("types") or ""

    try:
        if raw_types.strip().startswith("["):
            types = json.loads(raw_types)
        else:
            types = [t.strip() for t in raw_types.split(",") if t.strip()]
    except Exception:
        types = []

    if not (original or redacted):
        return abort(400, "original_text or redacted_text required")

    return render_template(
        "newreport.html",
        type_count=len(types),
        types=types,
        original=original,
        redacted=redacted,
        answer=answer
    )
    if request.method == "GET":
        # 수동 테스트용 빈 페이지
        return render_template(
            "newreport.html",
            type_count=0, types=[], original="", redacted="", answer=""
        )

    original = (request.form.get("original_text") or "")[:10000]
    redacted = (request.form.get("redacted_text") or "")[:10000]
    answer   = (request.form.get("answer_text")   or "")[:12000]
    raw_types = request.form.get("types") or ""

    try:
        if raw_types.strip().startswith("["):
            types = json.loads(raw_types)
        else:
            types = [t.strip() for t in raw_types.split(",") if t.strip()]
    except Exception:
        types = []

    if not (original or redacted):
        return abort(400, "original_text or redacted_text required")

    return render_template(
        "newreport.html",
        type_count=len(types),
        types=types,
        original=original,
        redacted=redacted,
        answer=answer
    )




@report_bp.route("/preview", methods=["GET","POST"])
def preview():
    if request.method == "GET":
        return render_template("report.html",
            type_count=0, types=[], original="", redacted="")
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
    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json(silent=True) or {}
    try: pii_count = int(data.get("piiCount") or 0)
    except: pii_count = 0
    by_type  = data.get("byType") or {}
    examples = data.get("examples") or {}

    redacted_data = (data.get("redactedData") or "")[:12000]

    if not isinstance(by_type, dict):
        by_type = {}
    else:
        by_type = {str(k): (int(v) if str(v).isdigit() else 0) for k, v in by_type.items()}
    types_list = [k for k, v in by_type.items() if v > 0]
    type_count = len(types_list)

    safe_examples_in = {}
    if isinstance(examples, dict):
        for k, arr in examples.items():
            vals = []
            if isinstance(arr, list):
                for v in arr[:2]:
                    vals.append(_mask_like(str(v)))
            safe_examples_in[str(k)] = vals

    items_for_llm = []
    for t, cnt in by_type.items():
        if cnt <= 0: 
            continue
        rule = RULES.get(t, {})
        ex_list = safe_examples_in.get(t) or []
        example = ex_list[0] if ex_list else f"[{t}_1]"
        items_for_llm.append({
            "pii_type": t,
            "count": cnt,
            "detector": rule.get("detector", "") or "정규표현식/NER/규칙",
            "evidence_hint": rule.get("evidence", ""),
            "example": example
        })

    paren, total_cnt = _format_counts(by_type)
    summary_prefix = f"총 개인정보가{paren} {total_cnt}건 탐지되었으며, " if total_cnt > 0 else ""

    try:
        import google.generativeai as genai
        api_key = os.getenv("GOOGLE_API_KEY", "").strip()
        if not api_key:
            return jsonify({"ok": False, "error": "GOOGLE_API_KEY not set"}), 500
        genai.configure(api_key=api_key)

        # ── sys_prompt: overall_risk / reason 제거, summary는 위험도 문구 없이 ──
        sys_prompt = """
역할: 개인정보 위험 분석 보고서 작성자.
입력으로는 '비식별 데이터(redactedData)'와 항목별 'detector'(탐지 방식 요약), 'evidence_hint'(분류 근거 힌트), 'example'(비식별 예시)가 제공됩니다.
원문 추정/복원 금지. 한국어, 짧고 명확한 문장. JSON 외 텍스트 출력 금지.

[출력 스키마]
{
  "summary": string,          // (1) 종합 설명. 각 문장은 \\n으로 구분. 첫 문장은 summary_prefix로 시작. (위험도 문구 넣지 않음)
  "combined_risk": string,    // 2가지 이상일 때 결합 위험과 사례 1문장 이상. 1가지면 비워도 됨.
  "findings": [
    {
      "pii_type": string,
      "count": number,
      "impact": string,           // (2) '유출되면 …'로 시작하는 1~2문장. 유형 고유의 위험만 간결히.
      "recommendation": string,   // (2) 유형 맞춤 명령형 한 문장. 예: "전화번호는 전체 마스킹 후 공유"
      "example": string,          // 입력 example 그대로 복사
      "evidence": string          // (1) 라벨/콜론 없이 자연어 한 문장(정규표현식/NER/검증 규칙을 요약)
    }
  ]
}

[작성 규칙]
- (1) 탐지 근거(evidence):
  • 라벨/콜론 없이 자연어 한 문장으로 작성. 예) "카드번호 길이와 Luhn 검사를 충족해 카드번호 형식으로 식별되었습니다."
- (2) 위험 설명/권장조치:
  • impact는 "유출되면"으로 시작하는 1~2문장. 일반론/정책 문구 금지.
  • recommendation은 즉시 취할 수 있는 행동을 명령형 한 문장으로.
- (3) 요약 설명(summary):
  • 유형이 1가지면: 해당 유형의 유출 위험 사례를 포함해 2문장.
  • 유형이 2가지 이상이면: 함께 유출될 때의 결합 위험을 사례와 함께 2~3문장.
  • 각 문장은 \\n으로 구분. 첫 문장은 summary_prefix로 시작.
- '유출될 경우' 대신 '유출되면'만 사용.
- '정책 준수/지속적 모니터링/신속 조치' 등 일반적 당위 문구 금지.
""".strip()

        user_prompt = (
            f"summary_prefix: {summary_prefix}\n"
            f"type_count: {type_count}\n"
            f"types: {json.dumps(types_list, ensure_ascii=False)}\n\n"
            f"[비식별 데이터]\n{redacted_data}\n\n"
            f"[항목 목록]\n{json.dumps(items_for_llm, ensure_ascii=False)}\n\n"
            f"[전체 통계]\n총 건수: {pii_count}, 유형별 건수: {json.dumps(by_type, ensure_ascii=False)}\n\n"
            "요청:\n"
            "- findings: example은 입력값 그대로 복사.\n"
            "- findings: evidence는 라벨/콜론 없이 자연어 한 문장으로 작성.\n"
            "- summary: 위 규칙에 따라 작성하고, 각 문장을 \\n으로 구분.\n"
        )

        model = genai.GenerativeModel("gemini-1.5-flash")
        generation_config = {"temperature": 0, "response_mime_type": "application/json"}
        resp = model.generate_content(
            [{"role":"user","parts":[sys_prompt+"\n\n"+user_prompt]}],
            generation_config=generation_config,
        )

        txt = ""
        try:
            txt = resp.text or ""
        except Exception:
            try:
                txt = resp.candidates[0].content.parts[0].text
            except Exception:
                txt = ""
        s = (txt or "").strip()
        if s.startswith("```"):
            s = s.strip("`")
            if "\n" in s: s = s.split("\n",1)[1]
        try:
            raw = json.loads(s)
        except Exception:
            raw = {}

        report = _validate_report(raw)

        if not report.get("combined_risk"):
            keys = set((by_type or {}).keys())
            if len(keys) >= 2:
                report["combined_risk"] = "여러 개인정보가 함께 노출되면 개인 식별과 접근이 쉬워져 표적 피싱이나 계정 탈취로 이어질 수 있습니다."
            else:
                report["combined_risk"] = ""

        items_by_type = {it["pii_type"]: it for it in items_for_llm}
        out_rows, seen = [], set()
        for f in report.get("findings", []):
            t = f.get("pii_type")
            if not t: 
                continue
            base = items_by_type.get(t, {})
            det = base.get("detector") or RULES.get(t, {}).get("detector", "")
            evh = base.get("evidence_hint") or RULES.get(t, {}).get("evidence", "")
            if not f.get("evidence"):
                # 자연어 한 문장
                if det or evh:
                    f["evidence"] = f"{(det+'을(를) 활용' if det else '규칙 기반')}해 검사했으며, {evh}로 {t} 형식과 일치합니다.".strip()
                else:
                    f["evidence"] = f"{t}의 일반적 패턴과 일치합니다."
            if not f.get("example"):
                f["example"] = base.get("example", f"[{t}_1]")
            out_rows.append(f); seen.add(t)

        for t, it in items_by_type.items():
            if t in seen: 
                continue
            det = it.get("detector") or RULES.get(t, {}).get("detector", "")
            evh = it.get("evidence_hint") or RULES.get(t, {}).get("evidence", "")
            out_rows.append({
                "pii_type": t,
                "count": it.get("count", 0),
                "impact": "",
                "recommendation": "",
                "example": it.get("example", f"[{t}_1]"),
                "evidence": (f"{(det+'을(를) 활용' if det else '규칙 기반')}해 검사했으며, {evh}로 {t} 형식과 일치합니다.").strip() if (det or evh) else f"{t}의 일반적 패턴과 일치합니다.",
            })

        report["findings"] = out_rows
        report["summary"] = _clean_sentence(report.get("summary",""))
        report["combined_risk"] = _clean_sentence(report.get("combined_risk",""))

        return jsonify({"ok": True, "report": report})

    except Exception as e:
        # 실패 시 안전 폴백
        fallback = {
            "summary": "자동 분석을 완료하지 못했습니다.\n탐지된 항목을 확인한 뒤 민감 정보는 공유 전에 제거하거나 마스킹하세요.",
            "combined_risk": "",
            "findings": [
                {
                    "pii_type": k,
                    "count": int(v or 0),
                    "impact": "",
                    "recommendation": "",
                    "example": f"[{k}_1]",
                    "evidence": RULES.get(k,{}).get("evidence",""),
                }
                for k, v in (by_type.items() if isinstance(by_type, dict) else [])
            ]
        }
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}", "report": _validate_report(fallback)}), 200
