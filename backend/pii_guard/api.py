from flask import Blueprint, request, jsonify
import io, base64, cv2, csv, json
import numpy as np
from PIL import Image
from typing import List, Dict, Any

from .card_ocr_redact import run_once_image
from .engine import detect_and_redact
from .pii_masking import mask_one as _mask_one_simple

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
        return jsonify({"ok": False, "error": str(e)}), 500

def _mask_text_value(v: Any, state: dict | None = None) -> str:
    s = str(v) if v is not None else ""
    res = detect_and_redact(s)
    return (res.get("redacted_text") or _mask_one_simple(s, state=state) or s)

def _collect_types_and_count(texts: List[str]) -> tuple[list, int]:
    all_types: set = set()
    total = 0
    for t in texts:
        r = detect_and_redact(t or "")
        all_types.update(r.get("types") or [])
        total += len(r.get("entities") or [])
    return sorted(all_types), total

@api_bp.route("/file-mask", methods=["POST", "OPTIONS"])
def file_mask():
    if request.method == "OPTIONS":
        return ("", 204)
    try:
        f = request.files.get("file")
        if not f:
            return jsonify({"ok": False, "error": "no file"}), 400

        name = f.filename or ""
        lower = name.lower()
        raw = f.read()

        preview_items: List[Dict[str, Any]] = []
        masked_file_base64 = None
        masked_mime = None
        masked_name = None
        types = []
        total_count = 0

        if lower.endswith(".csv"):
            sio = io.StringIO(raw.decode("utf-8", errors="ignore"))
            reader = csv.DictReader(sio)
            rows = list(reader)
            headers = list(reader.fieldnames or [])

            texts_for_stats = [" | ".join(str(row.get(h, "") or "") for h in headers) for row in rows]
            types, total_count = _collect_types_and_count(texts_for_stats)

            state = {}
            for i, row in enumerate(rows[:5]):
                orig_line = {h: row.get(h, "") for h in headers}
                masked_line = {h: _mask_text_value(row.get(h, ""), state=state) for h in headers}
                preview_items.append({"kind": "csv_row", "index": i, "original": orig_line, "masked": masked_line})

            out_sio = io.StringIO()
            w = csv.DictWriter(out_sio, fieldnames=headers)
            w.writeheader()
            state_all = {}
            for row in rows:
                w.writerow({h: _mask_text_value(row.get(h, ""), state=state_all) for h in headers})
            data = out_sio.getvalue().encode("utf-8")
            masked_file_base64 = base64.b64encode(data).decode("ascii")
            masked_mime = "text/csv"
            masked_name = f"masked_{name or 'data.csv'}"

        elif lower.endswith(".json") or lower.endswith(".jsonl"):
            text = raw.decode("utf-8", errors="ignore").strip()
            values_for_stats: List[str] = []
            preview_limit = 5
            state = {}

            def _mask_json_obj(obj):
                if isinstance(obj, dict):
                    return {k: _mask_json_obj(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [_mask_json_obj(v) for v in obj]
                else:
                    values_for_stats.append(str(obj))
                    return _mask_text_value(obj, state=state)

            if lower.endswith(".jsonl"):
                items = []
                for line in text.splitlines():
                    if not line.strip(): continue
                    try:
                        items.append(json.loads(line))
                    except:
                        items.append({"_raw": line})
                masked_items = [_mask_json_obj(o) for o in items]

                for i, (o, m) in enumerate(zip(items[:preview_limit], masked_items[:preview_limit])):
                    preview_items.append({"kind": "json_obj", "index": i, "original": o, "masked": m})

                out = "\n".join(json.dumps(o, ensure_ascii=False) for o in masked_items).encode("utf-8")
                masked_file_base64 = base64.b64encode(out).decode("ascii")
                masked_mime = "application/x-ndjson"
                masked_name = f"masked_{name or 'data.jsonl'}"
            else:
                try:
                    obj = json.loads(text)
                except:
                    return jsonify({"ok": False, "error": "bad json"}), 400
                masked = _mask_json_obj(obj)

                if isinstance(obj, list):
                  for i, (o, m) in enumerate(zip(obj[:preview_limit], masked[:preview_limit])):
                      preview_items.append({"kind": "json_item", "index": i, "original": o, "masked": m})
                elif isinstance(obj, dict):
                  keys = list(obj.keys())[:preview_limit]
                  for k in keys:
                      preview_items.append({"kind": "json_field", "path": k, "original": obj.get(k), "masked": masked.get(k)})
                else:
                  preview_items.append({"kind": "json_scalar", "original": obj, "masked": masked})

                out = json.dumps(masked, ensure_ascii=False, indent=2).encode("utf-8")
                masked_file_base64 = base64.b64encode(out).decode("ascii")
                masked_mime = "application/json"
                masked_name = f"masked_{name or 'data.json'}"

            types, total_count = _collect_types_and_count(values_for_stats)
        else:
            return jsonify({"ok": False, "error": "unsupported file type"}), 415

        return jsonify({
            "ok": True,
            "types": types,
            "total_count": int(total_count),
            "preview": preview_items[:5],
            "masked_base64": masked_file_base64,
            "masked_mime": masked_mime,
            "masked_name": masked_name,
            "original_name": name,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
