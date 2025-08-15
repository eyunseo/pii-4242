import re, unicodedata
from transformers import pipeline, AutoTokenizer, AutoModelForTokenClassification

LABELS_KOR = {
    "SSN":"주민등록번호","CC":"신용카드번호","PASS":"여권번호","DLN":"운전면허번호",
    "ACCT":"계좌번호","NAME":"이름","ADDR":"주소","PHONE":"전화번호","EMAIL":"이메일",
}

MODEL_DIR = r"C:\Users\USER\Downloads\pii_model_backup\content\drive\MyDrive\pii_model_final"
#MODEL_DIR = "/content/drive/MyDrive/pii_model_final"
tok = AutoTokenizer.from_pretrained(MODEL_DIR, use_fast=True)
model = AutoModelForTokenClassification.from_pretrained(MODEL_DIR)
ner = pipeline("ner", model=model, tokenizer=tok, aggregation_strategy="simple")

# 정규식- 여권번호, 운전면허번호, (금액 - negative 탐지)
PASS_RE = re.compile(r'(?<![A-Z0-9])[A-Z]\s?-?\d{8}(?=\D|$)')               # 여권: 문자1+숫자8
DLN_RE = re.compile(r'(?<!\d)(?:\d{2}-?\d{6}-?\d{2}|\d{12})(?=\D|$)')                 # 면허: 구형10/신형12
MONEY_RE = re.compile(r'(?<!\d)(\d{1,3}(?:,\d{3})*|\d+)\s*(원|만원|천원|억원|조원|KRW|₩)(?!\w)')

#여권번호 후보인지 확인하는 보조함수 
def looks_like_passport(s: str) -> bool:
    s = s.strip().replace(" ", "").replace("-", "")
    return bool(re.fullmatch(r'[A-Z]\d{8}', s))

#카드번호 후보인지 확인하는 보조함수 
def is_card_candidate(text: str) -> bool:
    digits = ''.join(ch for ch in text if ch.isdigit())
    return 12 <= len(digits) <= 19

#정규식 매칭 결과를 NER 엔티티 형식 리스트로 반환 
#엔티티의 시작과 끝을 마킹 
def add_regex_entities(text, pattern, tag):
    ents = []
    for m in pattern.finditer(text):
        ents.append({
            "entity_group": tag,
            "word": m.group(),
            "start": m.start(),
            "end": m.end(),
            "score": 1.0
        })
    return ents

#정규식 탐지 결과 + NER 결과를 합침, 중복 스팬을 제거해서 하나의 리스트로 만듦 
def merge_entities(text, ner_results):
    merged = ner_results.copy()
    merged += add_regex_entities(text, PASS_RE,  "PASS")
    merged += add_regex_entities(text, DLN_RE,   "DLN")
    merged += add_regex_entities(text, MONEY_RE, "MONEY")  # 금액 명시 추가

    seen, uniq = set(), []
    for e in sorted(merged, key=lambda x: (x.get("start",-1), x.get("end",-1), x.get("entity_group",""))):
        key = (e.get("start"), e.get("end"), e.get("entity_group"))
        if key not in seen:
            seen.add(key); uniq.append(e)
    return uniq

#모델이 민감정보를 조각난 여러 토큰으로 예측-> 하나로 합치기 
def merge_pass_dln_fragments(entities, text):
    merged, skip = [], set()
    ents = sorted(entities, key=lambda x: x.get('start', 0))
    for i, e in enumerate(ents):
        if i in skip: continue
        if e['entity_group'] in ('PASS', 'DLN'):
            start, end = e['start'], e['end']
            j = i + 1
            while j < len(ents) and ents[j].get('start', 0) <= end + 1:
                if ents[j]['entity_group'] in ('PASS', 'DLN', 'ACCT'):
                    end = max(end, ents[j]['end']); skip.add(j)
                j += 1
            merged.append({'entity_group': e['entity_group'], 'word': text[start:end],
                           'start': start, 'end': end, 'score': 1.0})
        else:
            merged.append(e)
    return merged

# 후처리 필터
def post_filter_entities(text: str, entities: list):
    # 잡힌 엔티티들중에서 금액 형식과 겹치는것 걸러냄.(금액 스팬 수집)
    money_spans = [(e["start"], e["end"]) for e in entities if e.get("entity_group") == "MONEY"]
    if not money_spans:
        money_spans = [(m.start(), m.end()) for m in MONEY_RE.finditer(text)]

    def overlaps(a_start, a_end, b_start, b_end):
        return not (a_end <= b_start or a_start >= b_end)

    filtered = []
    for e in entities:
        tag = e.get('entity_group')
        start = e.get('start', text.find(e['word']))
        end = e.get('end', start + len(e['word']))
        word = e['word']

        # 금액과 겹치거나 붙어 있으면 숫자형 엔티티 제거
        if tag in {'CC','PASS','DLN','ACCT'}:
            near_money = any(overlaps(start, end, ms, me) or abs(start - me) <= 1 for ms, me in money_spans)
            if near_money:
                continue
        # 여권번호, 카드는 이중검증 
        if tag == 'CC' and not is_card_candidate(word):
            continue
        if tag == 'PASS' and not looks_like_passport(word):
            continue

        filtered.append(e)
    return filtered

# 후처리할 조사 목록 
POSTPOSITIONS = ("으로", "까지", "부터", "에서", "으로", "하는", "와", "과", "은", "는", "이", "가", "도", "의", "로", "에")
#엔티티 끝에 붙은 한국어 조사 잘라내서 엔티티 스팬과 분리 
def trim_postpositions(entities, text):
    trimmed = []
    for e in entities:
        start, end = e["start"], e["end"]
        word = e["word"]
        #조사 제거-엔티티 끝에 지정된 조사 단어가 붙어있으면 삭제
        changed = True
        while changed:
            changed = False
            for pp in POSTPOSITIONS:
                if word.endswith(pp) and len(word) > len(pp):
                    if text[end - len(pp):end] == pp:
                        end -= len(pp)
                        word = word[:-len(pp)]
                        changed = True
                        break
        e["end"] = end
        e["word"] = word
        trimmed.append(e)
    return trimmed

#텍스트 정규화 함수 
def normalize_text(text):
    text = text.replace("－", "-").replace("–", "-")
    text = re.sub(r"\s+", " ", text)
    text = unicodedata.normalize("NFKC", text)
    return text

#마스킹 함수 
def mask_entities(text, entities):
    entities_sorted = sorted([e for e in entities if e.get('entity_group') != 'MONEY'], # 금액은 마스킹 목록에서 제회 
                             key=lambda x: x.get('start', text.find(x['word'])), reverse=True)
    masked_text, used = text, set()
    for ent in entities_sorted:
        start = ent.get('start'); end = ent.get('end')
        word = ent['word']; tag = ent['entity_group']
        kor_tag = LABELS_KOR.get(tag, tag)

        if start is None or end is None:
            masked_text = masked_text.replace(word, f"[{tag}]")
            continue
        if any(not (end <= s or start >= e) for s, e in used):  
            continue
        masked_text = masked_text[:start] + f"[{kor_tag}]" + masked_text[end:]
        used.add((start, end))
    return masked_text

# 순서대로 만든 파이프라인 
def mask_one(raw_text: str) -> str:
    text = normalize_text(raw_text)
    ner_results = ner(text)
    final = merge_entities(text, ner_results)
    final = merge_pass_dln_fragments(final, text)
    final = post_filter_entities(text, final)
    final = trim_postpositions(final, text)
    masked = mask_entities(text, final)
    return masked

if __name__ == "__main__":
    texts = [
        
    ]

    for i, text in enumerate(texts, 1):
            masked = mask_one(text)
            #print(f"[{i}] 원문: {raw}")
            print(f"마스킹: {masked}\n")


