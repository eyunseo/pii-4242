import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))
import json
from typing import Any, Dict, List

from pii_guard.parsers.json_parser import json_parser as JP_JSON
from pii_guard.parsers.csv_parser import csv_parser as JP_CSV

from pii_guard.pii_masking import mask_one

'''
JSON 파일을 로드한다. 
'''
def load_json(p: Path):
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)

'''
JSON 파일을 저장한다. 
'''
def save_json(p: Path, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

'''
*_parsed.json 파일을 읽어서 *_masked.json 파일을 생성한다.
stateful=True이면 파일 단위 state를 공유하여 인덱스를 누적한다.
'''
def mask_parsed_file(parsed_path: Path, out_suffix: str, stateful: bool = False) -> Path:
    rows = load_json(parsed_path)  # [{"text": "..."} ...]
    state = {} if stateful else None
    masked_rows = []
    for row in rows:
        t = row.get("text", "")
        masked_rows.append({"text": t, "masked": mask_one(t, state=state)})
    out = parsed_path.with_name(parsed_path.name.replace(out_suffix, "_masked.json"))
    save_json(out, masked_rows)
    return out

'''
JSON 파일을 복원한다. 
플랫(flat)한 path 기반 딕셔너리를 중첩 구조로 복원한다. 
(예: {"a.b": 1} → {"a": {"b": 1}})
'''
def unflatten(flat: Dict[str, Any], sep: str = ".") -> Dict[str, Any]:
    root: Any = {}
    for path, value in flat.items():
        keys = path.split(sep) if path else []
        cur = root
        for i, k in enumerate(keys):
            is_last = i == len(keys) - 1
            if is_last:
                if isinstance(cur, list):
                    raise TypeError("list container cannot take dict key")
                cur[k] = value
            else:
                if isinstance(cur, list):
                    raise TypeError("list container cannot take dict key")
                if k not in cur or not isinstance(cur[k], (dict, list)):
                    cur[k] = {}
                cur = cur[k]
    return root

'''
JSON 파싱 결과 파일(*_parsed.json)을 복원하여 저장한다.
본과 마스킹 값을 비교할 수 있는 *_overlay.json도 함께 생성한다.
'''
def restore_to_json(file_stem: str, result_dir: Path, file_dir: Path, joiner: str) -> bool:
    map_path    = result_dir / f"{file_stem}_map.json"
    in_json     = file_dir   / f"{file_stem}.json"

    if not in_json.exists():
        print(f"[warn] skip {file_stem}: original JSON not found -> {in_json}")
        return False

    maps = load_json(map_path) 
    _    = load_json(in_json)  

    state   = {}  # 파일 단위 인덱싱 공유
    restored: List[Dict[str, Any]] = []
    overlay:  List[Dict[str, Any]] = []

    for i, m in enumerate(maps):
        paths: List[str]      = m.get("paths", [])
        orig_parts: List[str] = m.get("parts", [])
        n = min(len(paths), len(orig_parts))

        # parts를 직접 stateful 마스킹
        masked_parts = [mask_one(str(orig_parts[k]), state=state) for k in range(n)]

        # path → masked 매핑 후 원래 구조로 복원
        flat_masked  = {paths[k]: masked_parts[k] for k in range(n)}
        rec_masked   = unflatten(flat_masked)
        restored.append(rec_masked)

        # overlay(원본/마스킹 페어)
        fields = []
        for k in range(n):
            fields.append({
                "path": paths[k],
                "original": orig_parts[k],
                "masked": masked_parts[k]
            })
        overlay.append({"index": i, "fields": fields})

    save_json(result_dir / f"{file_stem}_restored.json", restored)
    save_json(result_dir / f"{file_stem}_overlay.json", overlay)
    return True

'''
구버전 map에서 paths만 있는 경우 row/column 추출을 보조한다. 
(예: "row[3].name" → {"row": 3, "column": "name"})
'''
def _paths_to_fields(paths: List[str]) -> List[Dict[str, Any]]:
    out = []
    for p in paths:
        try:
            left, col = p.split(".", 1)
            r = int(left[left.find("[")+1:left.find("]")])
            out.append({"row": r, "column": col})
        except Exception:
            out.append({"path": p})
    return out

'''
JSON 파싱 결과 파일(*_parsed.json)을 복원하여 저장한다.
_map.json + 원본 CSV → *_restored.csv, *_overlay.json
'''
def restore_to_csv(file_stem: str, result_dir: Path, file_dir: Path, joiner: str) -> bool:
    import csv

    def _norm(s: str) -> str:
        return (s or "").strip().lstrip("\ufeff").replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")

    map_path    = result_dir / f"{file_stem}_map.json"
    in_csv      = file_dir   / f"{file_stem}.csv"

    if not in_csv.exists():
        print(f"[warn] skip {file_stem}: original CSV not found -> {in_csv}")
        return False

    maps = load_json(map_path)

    # 원본 CSV 로딩 + 헤더/별칭 준비
    with in_csv.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        original_headers = list(reader.fieldnames or [])
        header_alias = { _norm(h): h for h in original_headers }
        rows = list(reader)

    if not rows:
        print(f"[warn] empty csv: {in_csv}")
        return False

    # 파일 단위 인덱싱 state 공유
    state = {}

    # 컬럼별 허용 라벨
    label_whitelist: Dict[str, set] = {
        "주민등록번호": {"SSN"},
        "여권번호": {"PASS"},
        "신용카드번호": {"CC"},
        "계좌번호": {"ACCT"},
    }

    # 각 필드(original)에 대해 열 단위 마스킹 수행
    for m in maps:
        fields = m.get("fields") or _paths_to_fields(m.get("paths", []))
        for field in fields:
            r = field.get("row")
            c = field.get("column")
            if r is None or c is None or not (0 <= r < len(rows)):
                continue
            want = _norm(str(c))
            actual_col = header_alias.get(want)
            if not actual_col:
                print(f"[warn] column not found: want='{c}' (norm='{want}')")
                continue

            original_val = field.get("original", "")
            base_col = actual_col.strip().lstrip("\ufeff")
            allow = label_whitelist.get(base_col)
            rows[r][actual_col] = mask_one(str(original_val), state=state, allow_labels=allow)

    # 저장 (복원 CSV)
    out_csv = result_dir / f"{file_stem}_restored.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        import csv as _csv
        writer = _csv.DictWriter(f, fieldnames=original_headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({h: row.get(h, "") for h in original_headers})

    # 오버레이 (원본 vs 마스킹 비교표)
    overlay = []
    for m in maps:
        fields = m.get("fields") or _paths_to_fields(m.get("paths", []))
        fields_out = []
        for field in fields:
            r = field.get("row")
            c = field.get("column")
            want = _norm(str(c))
            actual_col = header_alias.get(want, c)
            base_col = (actual_col or "").strip().lstrip("\ufeff")
            original_val = field.get("original", "")
            allow = label_whitelist.get(base_col)
            masked_val   = mask_one(str(original_val), state=state, allow_labels=allow)
            fields_out.append({
                "path": f"row[{r}].{actual_col}" if r is not None and actual_col else (field.get("path", "") or ""),
                "original": original_val,
                "masked": masked_val
            })
        overlay.append({"fields": fields_out})

    save_json(result_dir / f"{file_stem}_overlay.json", overlay)
    return True

def main():
    # JSON, CSV 파서 상수
    JSON_FILE_DIR   = JP_JSON.FILE_DIR
    JSON_RESULT_DIR = JP_JSON.RESULT_DIR
    JSON_JOINER     = getattr(JP_JSON, "JOINER", " | ")
    JSON_SUFFIX     = getattr(JP_JSON, "OUT_SUFFIX", "_parsed.json")

    CSV_FILE_DIR    = JP_CSV.FILE_DIR
    CSV_RESULT_DIR  = JP_CSV.RESULT_DIR
    CSV_JOINER      = getattr(JP_CSV, "JOINER", " | ")
    CSV_SUFFIX      = getattr(JP_CSV, "OUT_SUFFIX", "_parsed.json")

    JP_JSON.main()
    json_parsed = sorted(JSON_RESULT_DIR.glob(f"*{JSON_SUFFIX}"))
    print(f"[json] RESULT_DIR={JSON_RESULT_DIR} | OUT_SUFFIX={JSON_SUFFIX} | found={len(json_parsed)}")
    for p in json_parsed:
        stem = p.name.replace(JSON_SUFFIX, "")
        mask_parsed_file(p, JSON_SUFFIX, stateful=True)
        restore_to_json(stem, JSON_RESULT_DIR, JSON_FILE_DIR, JSON_JOINER)

    JP_CSV.main()
    csv_parsed = sorted(CSV_RESULT_DIR.glob(f"*{CSV_SUFFIX}"))
    print(f"[csv]  RESULT_DIR={CSV_RESULT_DIR} | OUT_SUFFIX={CSV_SUFFIX} | found={len(csv_parsed)}")
    for p in csv_parsed:
        stem = p.name.replace(CSV_SUFFIX, "")
        mask_parsed_file(p, CSV_SUFFIX, stateful=True)
        restore_to_csv(stem, CSV_RESULT_DIR, CSV_FILE_DIR, CSV_JOINER)

if __name__ == "__main__":
    main()
