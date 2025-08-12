#아직은 정규표현식 기반 비식별화
import re

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"\b01[016789]-?\d{3,4}-?\d{4}\b")
CARD_RE  = re.compile(r"(?:\d[ -]*?){13,16}")

def detect_and_redact(text: str):
    entities = []
    def add(t, m): entities.append({"type": t, "value": m.group(0), "start": m.start(), "end": m.end()})

    for m in EMAIL_RE.finditer(text): add("이메일", m)
    for m in PHONE_RE.finditer(text): add("전화번호", m)
    for m in CARD_RE.finditer(text):  add("카드번호", m)

    red = text
    for e in sorted(entities, key=lambda x: x["start"], reverse=True):
        tag = f"[{e['type']}_1]"
        red = red[:e["start"]] + tag + red[e["end"]:]
    types = sorted({e["type"] for e in entities})
    return {"entities": entities, "types": types, "redacted_text": red}
