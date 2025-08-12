from flask import Blueprint, request, render_template, abort
import json

report_bp = Blueprint("report", __name__)

def _jload(s, default):
    if isinstance(s, (dict, list)): return s
    try: return json.loads(s or "")
    except: return default

@report_bp.route("/preview", methods=["GET", "POST"])
def preview():
    if request.method == "GET":
        # 주소창으로 테스트할 때: 샘플 예시 렌더
        return render_template(
            "report.html",
            type_count=3,
            types=["이름","전화번호","이메일"],
            original="예시 원문입니다. 이름 홍길동, 전화 010-1234-5678, 메일 a@b.com",
            redacted="예시 원문입니다. 이름 [이름_1], 전화 [전화번호_1], 메일 [이메일_1]",
        )

    # POST: 확장에서 실제 데이터가 넘어올 때
    original = (request.form.get("original_text") or "")[:10000]
    redacted = (request.form.get("redacted_text") or "")[:10000]
    types    = _jload(request.form.get("types"), [])
    if not original:
        return abort(400, "original_text required")

    return render_template(
        "report.html",
        original=original,
        redacted=redacted,
        types=types,
        type_count=len(types),
    )
