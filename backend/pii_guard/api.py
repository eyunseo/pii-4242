from flask import Blueprint, request, jsonify, make_response
import io, base64, cv2
import numpy as np 
from PIL import Image
from .card_ocr_redact import run_once, run_once_image
from .engine import detect_and_redact
from inspect import signature

api_bp = Blueprint("api", __name__)

@api_bp.route("/scan", methods=["POST", "OPTIONS"])
def scan():
    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json() or {}
    text = (data.get("text") or "").strip()
    result = detect_and_redact(text)
    return jsonify({"ok": True, "original_text": text, **result})

@api_bp.route("/ocr-mask", methods=["POST", "OPTIONS"])
def ocr_mask():
    if request.method == "OPTIONS":
        return ("", 204)
    try:
        f = request.files.get("file")
        if not f:
            return jsonify({"ok": False, "error": "no file"}), 400

        file_bytes = f.read()
        img_array = np.frombuffer(file_bytes, dtype=np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if img is None:
            return jsonify({"ok": False, "error": "bad image"}), 400

        # 폼 파라미터 파싱
        form = request.form
        def as_bool(v, default=False):
            if v is None: return default
            return str(v).strip().lower() in ("1","true","yes","on")

        langs            = (form.get("langs") or "eng+kor")
        fast             = as_bool(form.get("fast"), True)
        max_side         = int(form.get("max_side", "1200"))
        relaxed          = as_bool(form.get("relaxed"), True)
        upscale          = float(form.get("upscale", "1.3"))
        conf             = float(form.get("conf", "25"))
        name_conf        = float(form.get("name_conf", "8"))
        name_mode        = form.get("name_mode", "loose")
        cardnum_pad      = int(form.get("cardnum_pad", "24"))
        blur_margin      = int(form.get("blur_margin", "20"))
        blur_ksize       = int(form.get("blur_ksize", "61"))
        bottom_only      = as_bool(form.get("name_bottom_only"), False)
        draw_boxes       = as_bool(form.get("draw_boxes"), False)
        debug            = as_bool(form.get("debug"), False)

        res = run_once_image(
            img,
            lang_list=tuple(x.strip() for x in langs.split("+") if x.strip()),
            fast=fast,
            max_side=max_side,
            relaxed=relaxed,
            upscale=upscale,
            conf_th=conf,
            name_conf=name_conf,
            name_mode=name_mode,
            cardnum_pad=cardnum_pad,
            blur_margin=blur_margin,
            blur_ksize=blur_ksize,
            bottom_only=bottom_only,
            draw_boxes=draw_boxes,
            debug=debug,
        )

        red = res.get("image_redacted")
        if red is None:
            return jsonify({"ok": False, "error": "no redacted image"}), 500

        ok, buf = cv2.imencode(".png", red)
        if not ok:
            return jsonify({"ok": False, "error": "encode failed"}), 500
        masked_b64 = base64.b64encode(buf.tobytes()).decode("ascii")

        return jsonify({
            "ok": True,
            "masked_base64": masked_b64,
            "masked_mime": "image/png",
            "masked_name": f"masked_{f.filename or 'image'}.png",
            "meta": {
                "blur_boxes":  res.get("blur_boxes", []),
                "card_numbers":res.get("card_numbers", []),
                "expiry":      res.get("expiry", []),
                "names":       res.get("names", []),
            }
        })

    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
