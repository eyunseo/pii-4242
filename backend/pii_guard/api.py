from flask import Blueprint, request, jsonify, make_response
from .engine import detect_and_redact

api_bp = Blueprint("api", __name__)

@api_bp.route("/scan", methods=["POST", "OPTIONS"])
def scan():
    if request.method == "OPTIONS":
        # 프리플라이트 OK
        return ("", 204)

    data = request.get_json() or {}
    text = (data.get("text") or "").strip()
    result = detect_and_redact(text)
    return jsonify({"ok": True, "original_text": text, **result})