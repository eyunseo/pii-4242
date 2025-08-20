from __future__ import annotations
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Set

from transformers import AutoTokenizer, AutoModelForTokenClassification, pipeline
from faker import Faker

try:
    from kiwipiepy import Kiwi
    _KIWI_OK = True
except Exception:
    Kiwi = None
    _KIWI_OK = False

LABELS_KOR = {
    "SSN": "주민등록번호",
    "CC": "신용카드번호",
    "PASS": "여권번호",
    "DLN": "운전면허번호",
    "ACCT": "계좌번호",
    "NAME": "이름",
    "ADDR": "주소",
    "PHONE": "전화번호",
    "EMAIL": "이메일",
    "MONEY": "금액",
    "DATE": "날짜",
    "TIME": "시간",
}

faker = Faker("ko_KR")

MODEL_DIR = str((Path(__file__).parent / "models").resolve())
tokenizer = AutoTokenizer.from_pretrained(MODEL_DIR, use_fast=True)
model = AutoModelForTokenClassification.from_pretrained(MODEL_DIR)
ner = pipeline("ner", model=model, tokenizer=tokenizer, aggregation_strategy="simple")

kiwi = Kiwi() if _KIWI_OK else None

PHONE_PATTERNS = [re.compile(r"^010[- ]?\d{3,4}[- ]?\d{4}$")]
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

SSN_RE = re.compile(r"\b\d{6}-\d{7}\b")
CC_RE = re.compile(r"\b(?:\d{4}[- ]?){3}\d{4}\b")
ACCT_PATTERNS = [r"\b\d{10}\b", r"\b\d{12}\b", r"\b\d{6}-\d{2}-\d{6}\b", r"\b\d{3}-\d{3}-\d{6}\b"]
ACCT_RE = [re.compile(p) for p in ACCT_PATTERNS]
PASS_RE = re.compile(r"[A-Z]\d{8}")
DLN_RE = re.compile(r"\d{2}-\d{2}-\d{6}-\d{2}")

MONEY_RE = re.compile(r"\d+(원|만원|천원|억원|조원|KRW|₩)")
DATE_RE = [
    re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}"),
    re.compile(r"\d{1,2}\s*월\s*\d{1,2}\s*일"),
    re.compile(r"\d{2}[-/.]\d{1,2}[-/.]\d{1,2}"),
]
TIME_RE = [
    re.compile(r"([01]?\d|2[0-3]):[0-5]\d"),
    re.compile(r"([01]?\d|2[0-3]):[0-5]\d:[0-5]\d"),
]

def validate_name(name: str) -> bool:
    return bool(re.fullmatch(r"[가-힣A-Za-z]{2,}", name or ""))

def validate_ssn(ssn: str) -> bool:
    digits = [int(ch) for ch in ssn if ch.isdigit()]
    if len(digits) != 13:
        return False
    weights = [2,3,4,5,6,7,8,9,2,3,4,5]
    s = sum(w*d for w,d in zip(weights, digits[:-1]))
    check = (11 - (s % 11)) % 10
    return check == digits[-1]

def validate_phone(phone: str) -> bool:
    return bool(re.fullmatch(r"01(?:0|1|[6-9])-(?:\d{3}|\d{4})-\d{4}", phone or ""))

def validate_dln(dln: str) -> bool:
    return bool(re.fullmatch(r"\d{2}-\d{2}-\d{6}-\d{2}", dln or ""))

def validate_email(email: str) -> bool:
    if not email or "@" not in email: return False
    if ".." in email: return False
    regex = re.compile(r"^[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}$")
    return bool(regex.fullmatch(email))

def luhn_check(card_number: str) -> bool:
    digits = [int(d) for d in card_number if d.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    parity = len(digits) % 2
    for i, digit in enumerate(digits):
        if i % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0

def normalize_text(text: str) -> str:
    text = text.replace("–", "-").replace("ㅡ", "-")
    text = re.sub(r"\s+", " ", text)
    return unicodedata.normalize("NFKC", text)

def add_regex_entities(text: str, patterns, tag: str) -> List[Dict[str, Any]]:
    if isinstance(patterns, re.Pattern):
        patterns = [patterns]
    ents = []
    for p in patterns:
        if isinstance(p, str): p = re.compile(p)
        for m in p.finditer(text):
            ents.append({"entity_group": tag,"word": m.group(),"start": m.start(),"end": m.end(),"score": 1.0})
    return ents

def add_email_entities(text: str, existing_entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    new_entities = existing_entities.copy()
    existing_words = {e["word"] for e in existing_entities if e.get("entity_group") == "EMAIL"}
    for m in EMAIL_PATTERN.finditer(text):
        email = m.group()
        if email not in existing_words:
            new_entities.append({"entity_group": "EMAIL","word": email,"start": m.start(),"end": m.end(),"score": 1.0})
            existing_words.add(email)
    return new_entities

def merge_entities(text: str, ner_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged = []
    for e in ner_results:
        merged.append({"entity_group": e.get("entity_group") or e.get("label") or e.get("entity"),
                       "word": e.get("word", ""), "start": int(e.get("start", 0)),
                       "end": int(e.get("end", 0)), "score": float(e.get("score", 1.0)), "_source": "ner"})
    regex_ents = []
    regex_ents += add_regex_entities(text, PASS_RE, "PASS")
    regex_ents += add_regex_entities(text, DLN_RE, "DLN")
    regex_ents += add_regex_entities(text, SSN_RE, "SSN")
    regex_ents += add_regex_entities(text, CC_RE, "CC")
    regex_ents += add_regex_entities(text, ACCT_RE, "ACCT")
    regex_ents += add_regex_entities(text, MONEY_RE, "MONEY")
    regex_ents += add_regex_entities(text, DATE_RE, "DATE")
    regex_ents += add_regex_entities(text, TIME_RE, "TIME")
    for e in regex_ents: e["_source"] = "regex"
    merged.extend(regex_ents)
    return merged

def replace_entities_with_fake(text: str, entities: List[Dict[str, Any]], state: Dict[str, Any] | None = None) -> str:
    if state is None: state = {}
    fake_map = state.setdefault("fake_map", {})
    ents = sorted(entities, key=lambda x: x["start"])
    masked, offset = text, 0
    NON_PII_CONTEXT = ["구매시간","구매 일시","주문시간","주문 일시","결제시간","등록일","작성시간","생성일자"]

    for e in ents:
        label = e["entity_group"]; start, end = e["start"]+offset, e["end"]+offset
        value = masked[start:end]
        context_window = masked[max(0, start-5):min(len(masked), end+5)]
        if any(ctx in context_window for ctx in NON_PII_CONTEXT): continue
        if label in {"MONEY","DATE","TIME"}: continue

        if value in fake_map:
            fake_value = fake_map[value]
        else:
            fake_value = None
            if label == "NAME" and validate_name(value): fake_value = faker.name()
            elif label == "PHONE" and validate_phone(value): fake_value = f"010-{faker.random_number(digits=4, fix_len=True)}-{faker.random_number(digits=4, fix_len=True)}"
            elif label == "EMAIL" and validate_email(value): fake_value = faker.unique.email()
            elif label == "ADDR": fake_value = faker.address()
            elif label == "SSN" and validate_ssn(value): fake_value = faker.ssn()
            elif label == "CC":
                candidate = faker.credit_card_number()
                while not luhn_check(candidate):  
                    candidate = faker.credit_card_number()
                fake_value = candidate
                fake_value = re.sub(r"(\d{4})(?=\d)", r"\1-", fake_value)
            elif label == "ACCT": fake_value = str(faker.random_number(digits=12))
            elif label == "DLN" and validate_dln(value):
                fake_value = "{:02d}-{:02d}-{:06d}-{:02d}".format(
                    faker.random_int(10,99),faker.random_int(10,99),
                    faker.random_number(digits=6, fix_len=True),faker.random_int(10,99))
            elif label == "PASS": fake_value = "P"+str(faker.random_number(digits=8, fix_len=True))
            if fake_value: fake_map[value] = fake_value
            else: fake_value = value

        masked = masked[:start]+fake_value+masked[end:]
        offset += len(fake_value)-(end-start)
    return masked

def fake_one(raw_text: str, state: Dict[str, Any] | None = None, allow_labels: Set[str] | None = None) -> str:
    text = normalize_text(raw_text)
    final = merge_entities(text, ner(text))
    final = add_email_entities(text, final)
    if allow_labels: final = [e for e in final if e.get("entity_group") in allow_labels]
    return replace_entities_with_fake(text, final, state=state)

if __name__ == "__main__":
    samples = [
        "구매시간 2025-08-18 15:32 결제완료 / 카드번호 1234-5678-9012-3456",
        "이민형 010-1234-5678 / a@b.com / 600731-4994581 / 12-34-567890-12",
        "이메일 a@b.com 이 다시 등장, 새 메일 c@d.com",
    ]
    state: Dict[str, Any] = {}
    for s in samples: print(fake_one(s, state=state))
