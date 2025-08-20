from __future__ import annotations
import re
import unicodedata
from pathlib import Path
from collections import defaultdict
from typing import Any, Dict, List, Tuple, Set

from transformers import AutoTokenizer, AutoModelForTokenClassification, pipeline

try:
    from kiwipiepy import Kiwi 
    _KIWI_OK = True
except Exception:
    Kiwi = None  
    _KIWI_OK = False

# 라벨 이름과 한국어 매핑
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
}

# 모델 디렉터리 설정
MODEL_DIR = str((Path(__file__).parent / "models").resolve())
tokenizer = AutoTokenizer.from_pretrained(MODEL_DIR, use_fast=True)
model = AutoModelForTokenClassification.from_pretrained(MODEL_DIR)
ner = pipeline("ner", model=model, tokenizer=tokenizer, aggregation_strategy="simple")

kiwi = Kiwi() if _KIWI_OK else None

# 정규식/패턴 정의
PHONE_PATTERNS = [ re.compile(r"^010[- ]?\d{3,4}[- ]?\d{4}$") ]
EMAIL_PATTERN  = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
SIMPLE_EMAIL_PATTERN = EMAIL_PATTERN

SSN_RE  = re.compile(r"\b\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])-[1-4]\d{6}\b")
CC_RE   = re.compile(r"\b(?:\d{4}[- ]?){3}\d{4}\b")
ACCT_PATTERNS = [ r"\b\d{10}\b", r"\b\d{12}\b", r"\b\d{6}-\d{2}-\d{6}\b", r"\b\d{3}-\d{3}-\d{6}\b" ]
ACCT_RE = [ re.compile(p) for p in ACCT_PATTERNS ]
PASS_RE = re.compile(r"(?<![A-Z0-9])[A-Z]\s?-?\d{8}(?=\D|$)")

# DLN: 카드/계좌/시간 내부 매칭 방지 (숫자 경계)
DLN_RE  = re.compile(r"(?<!\d)(?:\d{2}-\d{2}-\d{6}-\d{2}|\d{12})(?!\d)")

MONEY_RE = re.compile(r"(?<!\d)(\d{1,3}(?:,\d{3})*|\d+)\s*(원|만원|천원|억원|조원|KRW|₩)(?!\w)")
DATE_RE  = [
    re.compile(r"(?<!\d)\d{4}(?:[-/]|년)\d{1,2}(?:[-/]|월)\d{1,2}일?(?=\D|$)"),
    re.compile(r"(?<!\d)\d{1,2}\s*월\s*\d{1,2}\s*일(?=\D|$)"),
    re.compile(r"(?<!\d)\d{2}[-/.]\d{1,2}[-/.]\d{1,2}(?=\D|$)"),
]
# HH:MM 또는 HH:MM:SS (24h)
TIME_RE = [
    re.compile(r"(?<!\d)([01]?\d|2[0-3]):[0-5]\d(?!\d)"),
    re.compile(r"(?<!\d)([01]?\d|2[0-3]):[0-5]\d:[0-5]\d(?!\d)"),
]

CARD_NEAR_RE = re.compile(r"\d{4}-\d{4}-\d{4}-\d{4}")

PUBLIC_INSTITUTIONS = ["시청","구청","경찰청","법원","우체국","도서관","초등학교","중학교","고등학교","공공기관"]
BUSINESS_LABELS     = ["주문번호","주문 번호","주문No","주문ID","부서번호","대표번호","내선번호"]

# 비-PII 컨텍스트 키워드 (이 주변 숫자는 PII가 아님)
NON_PII_CONTEXT = [
    "구매시간","구매 일시","구매일시","주문시간","주문 일시","주문일시",
    "결제시간","결제 일시","결제일시","등록일","작성시간","생성일자",
    "발급일","만료일","거래시간","접속시간","로그인시간","방문시간","예약시간"
]

''' 
텍스트를 정규화한다.
특수 기호를 표준 하이픈으로 치환, 공백 압축, 유니코드 NFKC 정규화를 적용한다.
'''
def normalize_text(text: str) -> str:
    text = text.replace("–", "-").replace("ㅡ", "-")
    text = re.sub(r"\s+", " ", text)
    text = unicodedata.normalize("NFKC", text)
    return text

'''
문자열이 여권번호 형식(A+8자리)인지 검사한다.
공백/하이픈을 제거한 뒤 정규식으로 검증한다.
'''
def looks_like_passport(s: str) -> bool:
    s = s.strip().replace(" ", "").replace("-", "")
    return bool(re.fullmatch(r"[A-Z]\d{8}", s))

'''
문자열이 카드번호 후보인지 간단히 판정한다.
숫자만 추출했을 때 길이가 12~19 사이면 후보로 본다.
'''
def is_card_candidate(text: str) -> bool:
    digits = "".join(ch for ch in text if ch.isdigit())
    return 12 <= len(digits) <= 19

'''
정규식 패턴 리스트를 적용하여 (start, end) 구간 목록을 반환한다.
'''
def find_spans(text: str, patterns: List[re.Pattern]) -> List[Tuple[int,int]]:
    spans: List[Tuple[int,int]] = []
    for p in patterns:
        for m in p.finditer(text):
            spans.append((m.start(), m.end()))
    return spans

'''
정규식 매칭 결과를 엔티티(dict) 리스트로 변환하여 추가한다.
- patterns: re.Pattern 또는 패턴 리스트
- tag: 부여할 라벨명
'''
def add_regex_entities(text: str, patterns, tag: str) -> List[Dict[str, Any]]:
    """patterns: re.Pattern 또는 [re.Pattern|str, ...] — 단일 패턴도 안전 처리"""
    if isinstance(patterns, re.Pattern):
        patterns = [patterns]
    compiled = [re.compile(p) if isinstance(p, str) else p for p in patterns]
    ents: List[Dict[str, Any]] = []
    for p in compiled:
        for m in p.finditer(text):
            ents.append({
                "entity_group": tag,
                "word": m.group(),
                "start": m.start(),
                "end": m.end(),
                "score": 1.0
            })
    return ents

'''
NER 결과에 이메일 정규식 매칭을 보강하여 EMAIL 엔티티를 추가한다.
이미 존재하는 이메일 문자열은 중복 추가하지 않는다.
'''
def add_email_entities(text: str, existing_entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    new_entities = existing_entities.copy()
    existing_words = {e["word"] for e in existing_entities if e.get("entity_group") == "EMAIL"}
    for m in EMAIL_PATTERN.finditer(text):
        email = m.group()
        if email in existing_words:
            continue
        new_entities.append({
            "entity_group": "EMAIL",
            "word": email,
            "start": m.start(),
            "end": m.end(),
            "score": 1.0
        })
        existing_words.add(email)
    return new_entities

'''
NER 엔티티와 정규식 엔티티를 병합한다.
동일 (start,end,label) 충돌 시 NER 결과를 우선한다.
점수/길이를 보조 기준으로 더 신뢰도 높은 것을 선택한다.
'''
def merge_entities(text: str, ner_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged = []
    for e in ner_results:
        merged.append({
            "entity_group": e.get("entity_group") or e.get("label") or e.get("entity"),
            "word": e.get("word", ""),
            "start": int(e.get("start", text.find(e.get("word", "")))),
            "end": int(e.get("end", 0) or (text.find(e.get("word", "")) + len(e.get("word", "")))),
            "score": float(e.get("score", 1.0)),
            "_source": "ner",
        })
    regex_ents = []
    regex_ents += add_regex_entities(text, PASS_RE, "PASS")
    regex_ents += add_regex_entities(text, DLN_RE,  "DLN")
    regex_ents += add_regex_entities(text, SSN_RE,  "SSN")
    regex_ents += add_regex_entities(text, CC_RE,   "CC")
    regex_ents += add_regex_entities(text, ACCT_RE, "ACCT")
    regex_ents += add_regex_entities(text, MONEY_RE,"MONEY")
    for e in regex_ents:
        e["_source"] = "regex"
    merged.extend(regex_ents)

    # 정확히 동일 (start,end,label) → NER 우선
    exact: dict[tuple[int,int,str], dict] = {}
    for e in merged:
        key = (e["start"], e["end"], e["entity_group"])
        if key not in exact:
            exact[key] = e
        else:
            cur = exact[key]
            if cur.get("_source") != "ner" and e.get("_source") == "ner":
                exact[key] = e
            else:
                if (e.get("score",1.0), e["end"]-e["start"]) > (cur.get("score",1.0), cur["end"]-cur["start"]):
                    exact[key] = e
    out = list(exact.values())
    return out

'''
여권/운전면허 엔티티가 조각난 경우 인접 조각을 병합한다.
필드 경계(|)를 넘어서 병합하지 않는다.
'''
def merge_pass_dln_fragments(entities: List[Dict[str, Any]], text: str) -> List[Dict[str, Any]]:
    merged, skip = [], set()
    ents = sorted(entities, key=lambda x: x.get("start", 0))
    for i, e in enumerate(ents):
        if i in skip:
            continue
        if e["entity_group"] in ("PASS", "DLN"):
            start, end = e["start"], e["end"]
            j = i + 1
            while j < len(ents) and ents[j].get("start", 0) <= end + 1:
                gap = text[end:ents[j]["start"]]
                if "|" in gap:  
                    break
                if ents[j]["entity_group"] in ("PASS", "DLN", "ACCT"):
                    end = max(end, ents[j]["end"])
                    skip.add(j)
                j += 1
            merged.append({
                "entity_group": e["entity_group"],
                "word": text[start:end],
                "start": start, "end": end, "score": 1.0
            })
        else:
            merged.append(e)
    return merged

'''
주민등록번호와 겹치는 계좌번호 라벨은 제거한다.
'''
def remove_account_if_ssn_overlap(entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ssn_spans = [(e["start"], e["end"]) for e in entities if e["entity_group"] == "SSN"]
    if not ssn_spans:
        return entities
    def overlap(a,b): return not (a[1] <= b[0] or a[0] >= b[1])
    out = []
    for e in entities:
        if e["entity_group"] == "ACCT" and any(overlap((e["start"],e["end"]), s) for s in ssn_spans):
            continue
        out.append(e)
    return out

'''
특정 라벨(prefer)이 존재하는 범위에서는 suppress 라벨을 제거한다.
'''
def prefer_label_over(entities: List[Dict[str, Any]], prefer="CC", suppress=("DLN","ACCT")) -> List[Dict[str, Any]]:
    spans = [(e["start"], e["end"]) for e in entities if e.get("entity_group") == prefer]
    if not spans:
        return entities
    def overlap(a,b): return not (a[1] <= b[0] or a[0] >= b[1])
    out = []
    for e in entities:
        if e.get("entity_group") in suppress and any(overlap((e["start"], e["end"]), s) for s in spans):
            continue
        out.append(e)
    return out

'''
지정한 범위 주변 문맥에 비-PII 키워드가 있는지 검사한다.
존재하면 해당 숫자 라벨을 민감정보로 보지 않는다.
'''
def context_has_non_pii(text: str, start: int, end: int, window: int = 18) -> bool:
    l = max(0, start - window)
    r = min(len(text), end + window)
    ctx = text[l:r]
    return any(kw in ctx for kw in NON_PII_CONTEXT)

'''
엔티티 후처리를 수행한다.
비-PII 문맥 제거, PHONE/EMAIL/ADDR의 간단 형식 검증,공공기관/업무성 키워드 주변 주소 제거를 진행한다. 
'''
def postprocess_entities(text: str, ents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for e in ents:
        tag = e["entity_group"]; word = e["word"].replace(" ",""); keep = True

        if tag in {"DLN","ACCT","CC","PASS","SSN","PHONE","EMAIL"}:
            if context_has_non_pii(text, e["start"], e["end"]):
                keep = False

        if keep and tag == "PHONE":
            keep = any(p.match(word) for p in PHONE_PATTERNS)
        if keep and tag == "EMAIL":
            keep = bool(EMAIL_PATTERN.fullmatch(word))
        if keep and tag == "ADDR":
            ctx = text[max(0, e["start"]-10): e["end"]+10]
            if any(kw in ctx for kw in PUBLIC_INSTITUTIONS):
                keep = False

        if keep:
            ctx2 = text[max(0, e["start"]-10): e["end"]+10]
            if any(kw in ctx2 for kw in BUSINESS_LABELS):
                keep = False

        if keep:
            out.append(e)
    return out

'''
문자열 내 금액 패턴의 (start, end) 구간을 반환한다.
'''
def find_money_spans(text: str) -> List[Tuple[int,int]]:
    return [(m.start(), m.end()) for m in MONEY_RE.finditer(text)]

'''
날짜처럼 보이는 패턴의 (start, end) 구간을 반환한다.
'''
def find_date_like_spans(text: str) -> List[Tuple[int,int]]:
    return find_spans(text, DATE_RE)

'''
시간 패턴의 (start, end) 구간을 반환한다.
'''
def find_time_spans(text: str) -> List[Tuple[int,int]]:
    return find_spans(text, TIME_RE)

'''
날짜/시간/금액과의 겹침을 고려해 민감정보 오탐을 줄인다.
날짜/시간과 겹치면 숫자 라벨 제거, 금액 인접 시 카드/여권/계좌/면허 제거를 진행한다.
카드/여권 형식을 간단 검증한다.
'''
def post_filter_entities(text: str, entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    money_spans = find_money_spans(text)
    date_spans  = find_date_like_spans(text)
    time_spans  = find_time_spans(text)

    def overlaps(a_start,a_end,b_start,b_end): return not (a_end <= b_start or a_start >= b_end)

    filtered: List[Dict[str, Any]] = []
    for e in entities:
        tag = e["entity_group"]
        start, end = e.get("start"), e.get("end")
        if start is None or end is None:
            s = text.find(e["word"]); start, end = s, s+len(e["word"])

        if tag in {"DLN","ACCT","CC","PASS","SSN","PHONE","EMAIL"}:
            if any(overlaps(start,end,ds,de) for ds,de in date_spans):
                continue
            if any(overlaps(start,end,ts,te) for ts,te in time_spans):
                continue

        if tag in {"CC","PASS","DLN","ACCT"}:
            near_money = any(overlaps(start,end,ms,me) or abs(start-me) <= 1 for ms,me in money_spans)
            if near_money:
                continue

        if tag == "CC" and not is_card_candidate(e["word"]):
            continue
        if tag == "PASS" and not looks_like_passport(e["word"]):
            continue

        filtered.append({"entity_group": tag, "word": e["word"], "start": start, "end": end, "score": e.get("score", 1.0)})
    return filtered

'''
우선 라벨(prefer) 영역과 겹치는 suppress 라벨을 제거한다.
'''
def remove_if_overlap_priority(entities: List[Dict[str, Any]], prefer: str = "SSN",
                               suppress: Tuple[str,...] = ("DLN","ACCT","CC","PASS")) -> List[Dict[str, Any]]:
    pref_spans = [(e["start"], e["end"]) for e in entities if e.get("entity_group") == prefer]
    if not pref_spans:
        return entities
    def overlap(a:Tuple[int,int], b:Tuple[int,int]) -> bool: return not (a[1] <= b[0] or a[0] >= b[1])
    out: List[Dict[str, Any]] = []
    for e in entities:
        if e.get("entity_group") in suppress:
            if any(overlap((e["start"],e["end"]), s) for s in pref_spans):
                continue
        out.append(e)
    return out

'''
같은 라벨이 서로 인접하거나 지정 간격(max_gap) 이내일 때 병합한다.
'''
def merge_adjacent_same_label(entities: List[Dict[str, Any]], label: str, text: str, max_gap: int = 1) -> List[Dict[str, Any]]:
    ents = sorted(entities, key=lambda x: x.get("start", 0))
    out: List[Dict[str, Any]] = []
    i = 0
    while i < len(ents):
        e = ents[i]
        if e.get("entity_group") == label:
            start, end = e["start"], e["end"]
            j = i + 1
            while j < len(ents) and ents[j].get("entity_group") == label and ents[j]["start"] <= end + max_gap:
                end = max(end, ents[j]["end"])
                j += 1
            out.append({
                "entity_group": label,
                "word": text[start:end],
                "start": start,
                "end": end,
                "score": 1.0
            })
            i = j
        else:
            out.append(e)
            i += 1
    return out

'''
라벨별 일관된 인덱스를 유지하며 텍스트를 마스킹한다.
state에 누적하여 파일 단위로 동일 값에 동일 인덱스를 부여한다.
MONEY/DATE 등 제외 라벨은 마스킹하지 않는다.
'''
def mask_entities_with_indexing(text: str, entities: List[Dict[str, Any]], state: Dict[str, Any] | None = None) -> str:
    EXCLUDE_LABELS = {"MONEY","DATE"}
    if state is None:
        label_value_map: Dict[str, Dict[str,int]] = defaultdict(dict)
        label_counter:   Dict[str,int] = defaultdict(int)
    else:
        label_value_map = state.setdefault("label_value_map", defaultdict(dict))
        label_counter   = state.setdefault("label_counter",   defaultdict(int))

    PRIORITY = { "SSN":3, "EMAIL":2, "PHONE":2, "ADDR":2, "NAME":2, "DLN":1, "ACCT":1, "CC":1, "PASS":1, "MONEY":0, "DATE":0 }

    ents = [e for e in entities if e.get("entity_group") not in EXCLUDE_LABELS]
    ents.sort(key=lambda x: (x.get("start",0), PRIORITY.get(x.get("entity_group"),0)), reverse=True)

    seen_email: set[str] = set()
    for e in reversed(ents):
        label = e["entity_group"]; value = e["word"]
        if label == "EMAIL":
            if not EMAIL_PATTERN.fullmatch(value): continue
            if value in seen_email: continue
            seen_email.add(value)
        if value not in label_value_map[label]:
            label_counter[label] += 1
            label_value_map[label][value] = label_counter[label]

    masked = text
    used_spans: List[Tuple[int,int]] = []
    for e in ents:
        label = e["entity_group"]; value = e["word"]
        start = e.get("start", text.find(value)); end = e.get("end", start + len(value))
        if any(not (end <= s or start >= t) for s,t in used_spans):  
            continue
        idx = label_value_map[label].get(value)
        if idx is None: 
            continue
        kor = LABELS_KOR.get(label, label)
        token = f"[{kor}_{idx}]"
        masked = masked[:start] + token + masked[end:]
        used_spans.append((start, end))
    return masked

'''
텍스트 한 건을 마스킹한다.
NER + 정규식 결과 병합 및 각종 후처리를 거친 뒤 마스킹한다.
state를 넘기면 파일 단위 인덱싱을 누적 유지한다.
allow_labels가 지정되면 해당 라벨만 유지한다.
'''
def mask_one(raw_text: str,
             state: Dict[str, Any] | None = None,
             allow_labels: Set[str] | None = None) -> str:
    text = normalize_text(raw_text)
    ner_results = ner(text)

    final = merge_entities(text, ner_results)
    final = add_email_entities(text, final)
    final = merge_pass_dln_fragments(final, text)
    final = postprocess_entities(text, final)
    final = post_filter_entities(text, final)
    final = remove_account_if_ssn_overlap(final)
    final = remove_if_overlap_priority(final, "SSN", ("DLN","ACCT","CC","PASS"))
    final = prefer_label_over(final, "CC", ("DLN", "ACCT"))  # 카드 우선
    final = merge_adjacent_same_label(final, "NAME", text, max_gap=1)
    if allow_labels is not None:
        final = [e for e in final if e.get("entity_group") in allow_labels]
    final = trim_postpositions_with_kiwi(final, text)

    masked = mask_entities_with_indexing(text, final, state=state)
    return masked

'''
형태소 분석이 가능하면 조사/어미(J/E류)를 잘라서 깔끔한 토큰 경계를 만든다.
'''
def trim_postpositions_with_kiwi(entities: List[Dict[str, Any]], text: str) -> List[Dict[str, Any]]:
    if not kiwi:
        return entities
    trimmed: List[Dict[str, Any]] = []
    for e in entities:
        start,end = e["start"], e["end"]; word = e["word"]
        try:
            a = kiwi.analyze(word)
            if not a or not a[0]:
                trimmed.append(e); continue
            morphs = a[0][1]; last = morphs[-1] if morphs else None
            if last and last[1].startswith(("J","E")) and len(morphs) > 1:
                cut = len(last[0]); end -= cut; word = word[:-cut]
        except Exception:
            pass
        e2 = e.copy(); e2["end"] = end; e2["word"] = word
        trimmed.append(e2)
    return trimmed

if __name__ == "__main__":
    samples = [
        "구매시간 2025-08-18 15:32 결제완료 / 카드 1234-5678-9012-3456",
        "이민형 010-1234-5678 / a@b.com / 600731-4994581",
        "이메일 a@b.com 이 다시 등장, 새 메일 c@d.com",
    ]
    state: Dict[str, Any] = {}
    for s in samples:
        print(mask_one(s, state=state))
