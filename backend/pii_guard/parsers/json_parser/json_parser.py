from pathlib import Path
import json
import re
from typing import Any, Dict, Iterable, List

# 파일 경로 정의
BASE_DIR = Path(__file__).resolve().parent
FILE_DIR = BASE_DIR / "file"
RESULT_DIR = BASE_DIR / "result"
INCLUDE_FILES: List[str] = [] 

# 모든 문자열 leaf 자동 수집 
KEYS: List[str] = []  
TEMPLATE: str | None = None
MASK_DIGITS: bool = False
JOINER: str = " | "
OUT_SUFFIX = "_parsed.json"

'''
파일 확장자의 JSON 여부를 반환한다.
'''
def is_jsonl(path: Path) -> bool:
    return path.suffix.lower() == ".jsonl"

'''
JSON/JSONL 파일을 읽어 dict 레코드 반복자를 생성한다.
비 dict는 {"_value": 값} 형태로 래핑한다.
'''
def read_records(path: Path) -> Iterable[Dict[str, Any]]:
    if is_jsonl(path):
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                yield obj if isinstance(obj, dict) else {"_value": obj}
        return

    with path.open("r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except Exception:
            data = {}

    if isinstance(data, list):
        for item in data:
            yield item if isinstance(item, dict) else {"_value": item}
    elif isinstance(data, dict):
        yield data
    else:
        yield {"_value": data}

'''
중첩된 dict/list 구조를 평탄화하여 경로 기반 딕셔너리로 변환한다.
'''
def flatten(obj: Any, prefix: str = "", sep: str = ".") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            np = f"{prefix}{sep}{k}" if prefix else str(k)
            out.update(flatten(v, np, sep))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            np = f"{prefix}{sep}{i}" if prefix else str(i)
            out.update(flatten(v, np, sep))
    else:
        out[prefix] = obj
    return out

'''
문자열 내 숫자를 마스킹 처리한다.
마지막 keep_last 자리수만 남기고 나머지는 *로 치환한다.
'''
def mask_digits(s: str, keep_last: int = 2) -> str:
    def _mask_run(m: re.Match) -> str:
        run = m.group(0)
        if len(run) <= keep_last:
            return "*" * len(run)
        return "*" * (len(run) - keep_last) + run[-keep_last:]
    return re.sub(r"\d+", _mask_run, s)


'''
레코드(dict)로부터 text 문자열을 생성한다.
- TEMPLATE 지정 시 placeholder 기반
- KEYS 지정 시 해당 key만 사용
- KEYS 비었으면 모든 문자열 leaf 자동 수집
'''
def build_text(record: Dict[str, Any]) -> str:
    def to_str(v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, (int, float, bool)):
            return str(v)
        return str(v)

    if TEMPLATE:
        placeholders = re.findall(r"\{([^{}]+)\}", TEMPLATE)
        values = {k: to_str(record.get(k, "")) for k in placeholders}
        text = TEMPLATE.format(**values)
        text = re.sub(r"\s{2,}", " ", text).strip(" |")
    elif KEYS:
        parts = []
        for k in KEYS:
            if k in record and record[k] not in (None, ""):
                parts.append(f"{k}: {to_str(record[k])}")
        text = JOINER.join(parts)
    else:
        flat = flatten(record)
        parts = []
        for _, v in flat.items():
            if isinstance(v, (str, int, float, bool)):
                parts.append(str(v))
        text = JOINER.join([p for p in parts if p])

    if MASK_DIGITS:
        text = mask_digits(text)
    return text.strip()

'''
레코드(dict)를 기반으로 text를 생성하고,
text와 함께 사용된 parts(값 목록)와 paths(경로 목록)도 반환한다.
'''
def build_text_and_map(record: Dict[str, Any]) -> tuple[str, list[str], list[str]]:
    def to_str(v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, (int, float, bool)):
            return str(v)
        return str(v)

    # TEMPLATE 모드
    if TEMPLATE:
        placeholders = re.findall(r"\{([^{}]+)\}", TEMPLATE)
        values = {k: to_str(record.get(k, "")) for k in placeholders}
        text = TEMPLATE.format(**values)
        text = re.sub(r"\s{2,}", " ", text).strip(" |")
        parts = [values.get(k, "") for k in placeholders]
        paths = placeholders[:]
        return text, parts, paths

    # KEYS 지정 모드
    if KEYS:
        parts, paths = [], []
        for k in KEYS:
            if k in record and record[k] not in (None, ""):
                parts.append(to_str(record[k]))
                paths.append(k)
        text = JOINER.join(parts)
        return text, parts, paths

    # 모든 문자열 leaf 자동 수집
    flat = flatten(record)
    parts, paths = [], []
    for k, v in flat.items():
        if isinstance(v, (str, int, float, bool)):
            s = to_str(v)
            if s != "":
                parts.append(s)
                paths.append(k)
    text = JOINER.join(parts)
    if MASK_DIGITS:
        text = mask_digits(text)
    return text.strip(), parts, paths

'''
단일 JSON/JSONL 파일을 처리하고 결과 파일을 저장한다。 
'''
def process_one_file(in_path: Path) -> Path | None:
    try:
        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        texts: List[Dict[str, str]] = []
        maps: List[Dict[str, Any]] = []

        out_path = RESULT_DIR / f"{in_path.stem}{OUT_SUFFIX}"
        map_path = RESULT_DIR / f"{in_path.stem}_map.json"

        for rec in read_records(in_path):
            if not isinstance(rec, dict):
                rec = {"_value": rec}
            text, parts, paths = build_text_and_map(rec)
            texts.append({"text": text})
            maps.append({"paths": paths, "parts": parts, "joiner": JOINER})

        with out_path.open("w", encoding="utf-8") as out_f:
            json.dump(texts, out_f, ensure_ascii=False, indent=2)

        with map_path.open("w", encoding="utf-8") as m_f:
            json.dump(maps, m_f, ensure_ascii=False, indent=2)

        return out_path
    except Exception:
        return None

def main() -> None:
    if INCLUDE_FILES:
        targets = [FILE_DIR / name for name in INCLUDE_FILES]
    else:
        targets = sorted([*FILE_DIR.glob("*.json"), *FILE_DIR.glob("*.jsonl")])

    if not targets:
        return

    for path in targets:
        if not path.exists():
            continue
        process_one_file(path)


if __name__ == "__main__":
    main()
