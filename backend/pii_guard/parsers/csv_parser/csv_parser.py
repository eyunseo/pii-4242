from __future__ import annotations
from pathlib import Path
import csv, json
from typing import Dict, List, Any, Iterable, Optional

# 기본 경로 및 입출력 디렉터리 정의
BASE_DIR   = Path(__file__).resolve().parent
FILE_DIR   = BASE_DIR / "file"
RESULT_DIR = BASE_DIR / "result"

# 처리 대상 파일 목록과 STRICT 모드 여부 정의
INCLUDE_FILES: List[str] = [] 
STRICT: bool = True

# 헤더명을 표준 키로 매핑 후, 매핑된 컬럼만 사용
COLMAP: Dict[str, str] = {
    "name":"name","이름":"name",
    "intro":"self_intro","self_intro":"self_intro","자기소개":"self_intro","설명":"self_intro",
    "자신을설명하는줄글":"self_intro","자신을 설명하는 줄글":"self_intro","한줄소개":"self_intro","한 줄 소개":"self_intro",
    "phone":"phone","전화번호":"phone","휴대폰":"phone","연락처":"phone",
    "email":"email","이메일":"email",
    "rrn":"rrn","주민등록번호":"rrn",
    "alien_reg_no":"alien_reg_no","외국인등록번호":"alien_reg_no",
    "passport":"passport","여권번호":"passport",
    "driver_license":"driver_license","운전면허번호":"driver_license",
    "credit_card":"credit_card","카드번호":"credit_card","신용카드번호":"credit_card",
    "bank_account":"bank_account","계좌번호":"bank_account",
    "address":"address","주소":"address",
}

# 출력에서 허용하는 표준 키 집합을 정의
ALLOWED_KEYS = {
    "name","self_intro","phone","email","rrn","alien_reg_no",
    "passport","credit_card","bank_account","driver_license","address"
}

# 텍스트 결합용 구분자와 출력 파일 접미사 정의
JOINER     = " | "          
OUT_SUFFIX = "_parsed.json"  

PREVIEW_ROWS = 3

'''
처리 대상 CSV 파일 목록을 반환한다.
INCLUDE_FILES가 비어 있으면 file 디렉터리의 모든 *.csv를 정렬하여 반환한다.
'''
def list_targets() -> List[Path]:
    return [FILE_DIR / n for n in INCLUDE_FILES] if INCLUDE_FILES else sorted(FILE_DIR.glob("*.csv"))

'''
지정한 CSV 파일을 읽어 딕셔너리 행을 순차적으로 생성한다.
'''
def read_csv_rows(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield row

'''
헤더 문자열을 정규화한다. (앞뒤 공백 제거, 소문자화, 중간 공백 제거)
'''
def normalize_header(h: str) -> str:
    return (h or "").strip().lower().replace(" ", "")

'''
실제 CSV 헤더 목록을 표준 키로 매핑하는 딕셔너리를 생성한다.
COLMAP 기준으로 변환하되, ALLOWED_KEYS에 포함된 컬럼만 유지한다.
'''
def build_colmap(headers: List[str]) -> Dict[str, str]:
    m: Dict[str, str] = {}
    for h in headers:
        hn = normalize_header(h)
        if hn in COLMAP:
            std_key = COLMAP[hn]
            if std_key in ALLOWED_KEYS:
                m[h] = std_key
    return m

'''
주어진 데이터를 JSON 파일로 저장한다. (ensure_ascii로 한글 깨짐 방지)
'''
def save_json(p: Path, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

'''
단일 CSV 파일을 파싱하여 다음 산출물을 생성한다.
- parsed JSON: 각 행을 JOINER로 결합한 "text" 필드 리스트
- map JSON: 원본 필드 위치 및 값 메타데이터
'''
def process_one_csv(path: Path) -> Optional[Path]:
    try:
        rows = list(read_csv_rows(path))
        headers = list(rows[0].keys()) if rows else []
        header_map = build_colmap(headers) if STRICT else {h: h for h in headers}

        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        parsed_path = RESULT_DIR / f"{path.stem}{OUT_SUFFIX}"
        map_path    = RESULT_DIR / f"{path.stem}_map.json"

        parsed: List[Dict[str, str]] = []
        maps:   List[Dict[str, Any]] = []
        previews: List[str] = []

        # 사용할 컬럼 집합(순서 보장)
        use_cols = list(header_map.keys()) if header_map else headers

        for i, row in enumerate(rows):
            parts = []
            fields = []
            for col in use_cols:
                val = row.get(col, "")
                if val is None: val = ""
                if isinstance(val, str): val = val.strip()
                parts.append(str(val))
                fields.append({"row": i, "column": col, "original": val})

            text = JOINER.join(parts)
            parsed.append({"text": text})
            maps.append({"fields": fields, "joiner": JOINER})

            if len(previews) < PREVIEW_ROWS:
                previews.append(text)

        save_json(parsed_path, parsed)
        save_json(map_path, maps)
        return parsed_path

    except Exception:
        return None

def main() -> None:
    targets = list_targets()
    if not targets:
        return
    for csv_path in targets:
        if not csv_path.exists():
            continue
        process_one_csv(csv_path)

if __name__ == "__main__":
    main()
