from __future__ import annotations
import io, csv, json, base64
from typing import Any, Dict, List, Tuple
from pathlib import Path

from .pii_masking import (
    ner, mask_one, LABELS_KOR, normalize_text,
)

def _ner_entities(text: str) -> List[Dict[str, Any]]:
    text_norm = normalize_text(text)
    raw = ner(text_norm) 
    ents: List[Dict[str, Any]] = []
    for e in raw:
        label = e.get("entity_group") or e.get("label") or e.get("entity") or ""
        ents.append({
            "label": label,
            "type": LABELS_KOR.get(label, label),  
            "word": e.get("word", ""),
            "start": int(e.get("start", 0)),
            "end": int(e.get("end", 0)),
            "score": float(e.get("score", 1.0)),
            "_source": "ner",
        })
    return ents


def _collect_types_and_count(texts: List[str]) -> Tuple[List[str], int]:
    tset = set()
    total = 0
    for t in texts:
        for e in _ner_entities(t):
            tset.add(e["type"])
            total += 1
    return sorted(tset), total

def detect_and_redact(text: str) -> Dict[str, Any]:
    text_norm = normalize_text(text or "")
    entities = _ner_entities(text_norm)
    types = sorted({e["type"] for e in entities})

    redacted = mask_one(text_norm, state=None)

    return {
        "ok": True,
        "original_text": text,
        "redacted_text": redacted,
        "entities": entities,
        "types": types,
    }

def mask_csv_bytes(name: str, data: bytes) -> Dict[str, Any]:
    sio = io.StringIO(data.decode("utf-8", errors="ignore"))
    reader = csv.DictReader(sio)
    rows = list(reader)
    headers = list(reader.fieldnames or [])

    row_texts = [" | ".join(str(r.get(h, "") or "") for h in headers) for r in rows]
    types, total = _collect_types_and_count(row_texts)

    state: Dict[str, Any] = {}
    preview: List[Dict[str, Any]] = []
    for i, row in enumerate(rows[:5]):
        orig = {h: row.get(h, "") for h in headers}
        masked = {h: mask_one(str(row.get(h, "") or ""), state=state) for h in headers}
        preview.append({"kind": "csv_row", "index": i, "original": orig, "masked": masked})

    out_sio = io.StringIO()
    w = csv.DictWriter(out_sio, fieldnames=headers)
    if headers:
        w.writeheader()
    state_all: Dict[str, Any] = {}
    for row in rows:
        w.writerow({h: mask_one(str(row.get(h, "") or ""), state=state_all) for h in headers})
    out_bytes = out_sio.getvalue().encode("utf-8")

    return {
        "ok": True,
        "original_name": name,
        "types": types,
        "total_count": int(total),
        "preview": preview,
        "masked_base64": base64.b64encode(out_bytes).decode("ascii"),
        "masked_mime": "text/csv",
        "masked_name": f"masked_{name or 'data.csv'}",
    }


def mask_json_bytes(name: str, data: bytes, is_jsonl: bool = False) -> Dict[str, Any]:
    text = data.decode("utf-8", errors="ignore").strip()

    preview_limit = 5
    state: Dict[str, Any] = {}
    preview: List[Dict[str, Any]] = []
    values_for_stats: List[str] = []

    def _mask_json(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: _mask_json(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [_mask_json(v) for v in obj]
        else:
            values_for_stats.append(str(obj))
            return mask_one(str(obj), state=state)

    if is_jsonl:
        items: List[Any] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                items.append(json.loads(line))
            except Exception:
                items.append({"_raw": line})
        masked_items = [_mask_json(o) for o in items]

        for i, (o, m) in enumerate(zip(items[:preview_limit], masked_items[:preview_limit])):
            preview.append({"kind": "json_obj", "index": i, "original": o, "masked": m})

        out_text = "\n".join(json.dumps(o, ensure_ascii=False) for o in masked_items)
        out_bytes = out_text.encode("utf-8")
        masked_mime = "application/x-ndjson"
        masked_name = f"masked_{name or 'data.jsonl'}"
    else:
        try:
            obj = json.loads(text)
        except Exception:
            obj = text  

        masked = _mask_json(obj)

        if isinstance(obj, list):
            for i, (o, m) in enumerate(zip(obj[:preview_limit], masked[:preview_limit])):
                preview.append({"kind": "json_item", "index": i, "original": o, "masked": m})
        elif isinstance(obj, dict):
            for k in list(obj.keys())[:preview_limit]:
                preview.append({"kind": "json_field", "path": k, "original": obj.get(k), "masked": masked.get(k)})
        else:
            preview.append({"kind": "json_scalar", "original": obj, "masked": masked})

        out_bytes = json.dumps(masked, ensure_ascii=False, indent=2).encode("utf-8")
        masked_mime = "application/json"
        masked_name = f"masked_{name or 'data.json'}"

    types, total = _collect_types_and_count(values_for_stats)

    return {
        "ok": True,
        "original_name": name,
        "types": types,
        "total_count": int(total),
        "preview": preview,
        "masked_base64": base64.b64encode(out_bytes).decode("ascii"),
        "masked_mime": masked_mime,
        "masked_name": masked_name,
    }
