import argparse, os, re, warnings
import cv2
import numpy as np
import easyocr

warnings.filterwarnings("ignore", message=".*pin_memory.*")

EXP_PAT = re.compile(r"\b(0[1-9]|1[0-2])[\/\-\.\s]?(?:20)?(\d{2})\b")   # MM/YY | MMYY | MM-YY | MM.YY
NAME_STOP = {"THRU","GOOD","VALID","CARD","HOLDER","CARDHOLDER"}

BRAND_WORDS = {
    "VISA","SIGNATURE","MASTERCARD","DISCOVER","AMERICAN","EXPRESS","AMEX",
    "UNIONPAY","WORLD","GOOD","DAY","KB","KBCARD","PLATINUM","GOLD","SILVER","INFINITE"
}
BRAND_PHRASES = [
    re.compile(r"\bVISA\b", re.I),
    re.compile(r"\bSIGNATURE\b", re.I),
    re.compile(r"\bAMERICAN\s+EXPRESS\b", re.I),
    re.compile(r"\bUNION\s*PAY\b", re.I),
]

def is_brand_text(s: str) -> bool:
    if not s: return False
    up = s.upper()
    toks = [t for t in re.split(r"[^A-Za-zØ\-]+", up) if t]
    if any(t in BRAND_WORDS for t in toks):
        return True
    return any(p.search(s) for p in BRAND_PHRASES)

SUBS = str.maketrans({'S':'5','s':'5','O':'0','o':'0','I':'1','i':'1','l':'1','|':'1','B':'8','b':'8',
                      'Z':'2','z':'2','G':'6','g':'6','Q':'0','D':'0'})

def luhn_check(number: str) -> bool:
    n = re.sub(r"\D", "", number or "")
    if not n.isdigit(): return False
    s, alt = 0, False
    for ch in n[::-1]:
        d = ord(ch) - 48
        if alt: d = d*2 - 9 if d*2 > 9 else d*2
        s += d; alt = not alt
    return s % 10 == 0

def mask_card_number(num: str) -> str:
    n = re.sub(r"\D","", num or "")
    if len(n) < 10: return "*" * len(n)
    return f"{n[:6]}{'*'*(len(n)-10)}{n[-4:]}"

def guess_brand(num: str) -> str:
    n = re.sub(r"\D","", num or "")
    try:
        if len(n)==15 and (n.startswith("34") or n.startswith("37")): return "American Express"
        if n.startswith("4") and len(n) in (13,16,19): return "Visa"
        if len(n)==16 and (51<=int(n[:2])<=55 or 2221<=int(n[:4])<=2720): return "Mastercard"
        if len(n) in (16,19) and (n.startswith("6011") or n.startswith("65")
            or (len(n)>=3 and 644<=int(n[:3])<=649)
            or (len(n)>=6 and 622126<=int(n[:6])<=622925)):
            return "Discover"
    except: pass
    return "Unknown"

def normalize_digitish(t: str) -> str:
    t = (t or "").strip()
    t = t.replace('—','-').replace('–','-').replace('~','-').replace('_',' ')
    t = t.translate(SUBS)
    t = re.sub(r"[^0-9\s/\-\.]", "", t)
    return re.sub(r"[^\d]", "", t)

def rect_from_box(b):
    try:
        pts = np.array(b, dtype=np.int32)
        if pts.shape != (4,2): return None
        x,y,w,h = cv2.boundingRect(pts); return x,y,w,h
    except: return None

def normalize_xyxy_or_xywh(r):
    if r is None: return None
    x, y, a, b = r
    if a > x and b > y and (a - x > 0) and (b - y > 0):  # xyxy
        return (x, y, a - x, b - y)
    return (x, y, max(0, a), max(0, b))  # xywh

def uniq_rects_xywh(rects):
    seen=set(); out=[]
    for r in rects:
        x,y,w,h = normalize_xyxy_or_xywh(r)
        key=(x,y,w,h)
        if key in seen: continue
        seen.add(key); out.append((x,y,w,h))
    return out

def same_textline(a, b, tol_ratio=0.6):
    ax,ay,aw,ah = a; bx,by,bw,bh = b
    return abs((ay+ah/2) - (by+bh/2)) <= max(ah, bh) * tol_ratio

def expand_rect(r, m, W, H):
    x,y,w,h = r
    return (max(0,x-m), max(0,y-m),
            min(W, x+w+m) - max(0,x-m),
            min(H, y+h+m) - max(0,y-m))

def perspective_fix(image):
    img=image.copy()
    g=cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    g=cv2.GaussianBlur(g,(3,3),0)
    e=cv2.Canny(g,50,150)
    cnts,_=cv2.findContours(e, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts: return image
    cnt=max(cnts, key=cv2.contourArea)
    peri=cv2.arcLength(cnt, True)
    approx=cv2.approxPolyDP(cnt, 0.02*peri, True)
    if len(approx)!=4: return image
    pts=approx.reshape(4,2).astype(np.float32)
    s=pts.sum(1); d=np.diff(pts, axis=1).reshape(-1)
    ordered=np.zeros((4,2), dtype=np.float32)
    ordered[0]=pts[np.argmin(s)]
    ordered[2]=pts[np.argmax(s)]
    ordered[1]=pts[np.argmin(d)]
    ordered[3]=pts[np.argmax(d)]
    (tl,tr,br,bl)=ordered
    wA=np.linalg.norm(br-bl); wB=np.linalg.norm(tr-tl)
    hA=np.linalg.norm(tr-br); hB=np.linalg.norm(tl-bl)
    W,H=int(max(wA,wB)), int(max(hA,hB))
    M=cv2.getPerspectiveTransform(ordered, np.array([[0,0],[W-1,0],[W-1,H-1],[0,H-1]], np.float32))
    return cv2.warpPerspective(img, M, (W,H))

def auto_deskew_by_hough(gray):
    edges=cv2.Canny(gray,50,150)
    lines=cv2.HoughLines(edges,1,np.pi/180,120)
    if lines is None: return gray
    angles=[]
    for l in lines[:100]:
        _,theta=l[0]; deg=theta*180/np.pi
        if deg<10 or deg>170:
            if deg>90: deg-=180
            angles.append(deg)
    if not angles: return gray
    angle=float(np.median(angles))
    h,w=gray.shape[:2]
    M=cv2.getRotationMatrix2D((w//2,h//2), angle, 1.0)
    return cv2.warpAffine(gray,M,(w,h),flags=cv2.INTER_LINEAR,borderMode=cv2.BORDER_REPLICATE)

def preprocess(img, strong=False, upscale=1.4, do_deskew=True):
    if upscale and upscale!=1.0:
        h,w=img.shape[:2]
        img=cv2.resize(img,(int(w*upscale), int(h*upscale)), interpolation=cv2.INTER_CUBIC)
    gray=cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray=cv2.createCLAHE(2.0,(8,8)).apply(gray)
    if do_deskew: gray=auto_deskew_by_hough(gray)
    if strong: gray=cv2.bilateralFilter(gray,5,30,30)
    return img, gray

def boost_name_contrast(gray):
    se = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25))
    whitehat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, se)
    blur = cv2.GaussianBlur(whitehat, (0,0), 1.2)
    sharp = cv2.addWeighted(whitehat, 1.6, blur, -0.6, 0)
    g = np.clip(((sharp/255.0) ** 0.8) * 255, 0, 255).astype(np.uint8)
    return g

def enhance_embossed_digits(gray):
    k=cv2.getStructuringElement(cv2.MORPH_RECT,(25,7))
    blackhat=cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, k)
    sobelx=cv2.Sobel(gray, cv2.CV_32F, 1,0, ksize=3); sobelx=cv2.convertScaleAbs(sobelx)
    mix=cv2.addWeighted(blackhat,0.7, sobelx,0.5, 0)
    mix=cv2.GaussianBlur(mix,(3,3),0)
    th=cv2.threshold(mix,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)[1]
    line_k=cv2.getStructuringElement(cv2.MORPH_RECT,(15,3))
    th=cv2.morphologyEx(th, cv2.MORPH_CLOSE, line_k, iterations=2)
    return th

def digit_line_boxes_from_bin(binimg, min_area=600, min_ar=3.5):
    cnts,_=cv2.findContours(binimg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes=[]; H,W=binimg.shape[:2]
    for c in cnts:
        x,y,w,h=cv2.boundingRect(c)
        if w*h<min_area: continue
        ar=w/float(h+1e-6)
        if ar<min_ar: continue
        x=max(0,x-3); y=max(0,y-2)
        w=min(W-x,w+6); h=min(H-y,h+4)
        boxes.append((x,y,x+w,y+h))
    if boxes:
        from imutils.object_detection import non_max_suppression
        boxes=non_max_suppression(np.array(boxes))
        boxes=[tuple(map(int,b)) for b in boxes]
    return boxes

def east_boxes(image, east_model_path, min_conf=0.5, width=1280, height=736):
    from imutils.object_detection import non_max_suppression
    (H,W)=image.shape[:2]
    resized=cv2.resize(image,(width,height))
    blob=cv2.dnn.blobFromImage(resized,1.0,(width,height),(123.68,116.78,103.94),swapRB=True,crop=False)
    net=cv2.dnn.readNet(east_model_path)
    net.setInput(blob)
    (scores, geometry)=net.forward(["feature_fusion/Conv_7/Sigmoid","feature_fusion/concat_3"])
    (numRows,numCols)=scores.shape[2:4]
    rects, confs=[], []
    for y in range(numRows):
        scoresData=scores[0,0,y]; xData0=geometry[0,0,y]; xData1=geometry[0,1,y]
        xData2=geometry[0,2,y]; xData3=geometry[0,3,y]; angles=geometry[0,4,y]
        for x in range(numCols):
            if scoresData[x]<min_conf: continue
            offsetX, offsetY = x*4.0, y*4.0
            angle=angles[x]; cos=np.cos(angle); sin=np.sin(angle)
            h=xData0[x]+xData2[x]; w=xData1[x]+xData3[x]
            endX=int(offsetX + (cos*xData1[x]) + (sin*xData2[x]))
            endY=int(offsetY - (sin*xData1[x]) + (cos*xData2[x]))
            startX=int(endX - w); startY=int(endY - h)
            rects.append((startX,startY,endX,endY)); confs.append(float(scoresData[x]))
    boxes=non_max_suppression(np.array(rects), probs=np.array(confs))
    return [(int(sx),int(sy),int(ex),int(ey)) for (sx,sy,ex,ey) in boxes]

def _map_langs_for_easyocr(lang_list):
    m={"eng":"en","en":"en","kor":"ko","ko":"ko","jpn":"ja","ja":"ja","chi_sim":"ch_sim","ch_sim":"ch_sim"}
    out=[]
    for l in lang_list: out.append(m.get(l.lower(), l.lower()))
    return list(dict.fromkeys(out))

def _to_items(rs, conf_min):
    out=[]
    for (box,text,conf) in rs:
        try:
            quad=[(int(box[0][0]),int(box[0][1])),
                  (int(box[1][0]),int(box[1][1])),
                  (int(box[2][0]),int(box[2][1])),
                  (int(box[3][0]),int(box[3][1]))]
            if float(conf)*100.0 >= conf_min and rect_from_box(quad) is not None:
                out.append((quad,(text or "").strip(), float(conf)*100.0))
        except:
            pass
    return out

def easyocr_items(reader, img, allowlist=None, min_size=5, text_th=0.5, low_text=0.3,
                  canvas_size=2560, mag_ratio=2.0, decoder='greedy'):
    rs=reader.readtext(img, detail=1, paragraph=False,
                       allowlist=allowlist, min_size=min_size,
                       text_threshold=text_th, low_text=low_text,
                       canvas_size=canvas_size, mag_ratio=mag_ratio,
                       decoder=decoder)
    return rs

def is_name_candidate(text: str) -> bool:
    if not text: return False
    t = text.strip()
    if any(ch.isdigit() for ch in t):
        return False
    if len(t.replace(" ", "")) < 2:
        return False
    if any(tok in NAME_STOP for tok in t.upper().split()):
        return False
    if re.fullmatch(r"[A-Za-z][A-Za-z\.\-'\s]+", t):  # 영문 + .-'
        return True
    if re.fullmatch(r"(?:[A-Za-z]\.?\s?)+[A-Z][A-Za-z\-']+", t):  # 이니셜+성
        return True
    if re.fullmatch(r"[가-힣][가-힣\s·\-]+", t):
        return True
    letters = sum(ch.isalpha() for ch in t)
    return letters / max(1, len(t.replace(" ", ""))) >= 0.6

def build_text_lines(ocr_items, imgW, imgH):
    toks=[]
    for i,(box,text,conf) in enumerate(ocr_items):
        if not text: continue
        r=rect_from_box(box)
        if r is None: continue
        x,y,w,h=r; toks.append((i,x,y,w,h,text.strip()))
    toks.sort(key=lambda t:(t[2],t[1]))
    lines=[]; i=0
    while i<len(toks):
        idx_i,x,y,w,h,t=toks[i]
        seq=[(idx_i,t,(x,y,w,h))]; j=i+1
        while j<len(toks):
            idx_j,xj,yj,wj,hj,tj=toks[j]
            if same_textline((x,y,w,h),(xj,yj,wj,hj)):
                seq.append((idx_j,tj,(xj,yj,wj,hj))); j+=1
            else: break
        text_join=" ".join([t for _,t,_ in seq]).strip()
        if text_join:
            x1=min(x for _,_,(x,y,w,h) in seq); y1=min(y for _,_,(x,y,w,h) in seq)
            x2=max(x+w for _,_,(x,y,w,h) in seq); y2=max(y+h for _,_,(x,y,w,h) in seq)
            x1=max(0, x1-8); y1=max(0, y1-4); x2=min(imgW, x2+8); y2=min(imgH, y2+4)
            lines.append({"text":text_join,"idxs":[idx for idx,_,_ in seq],"bbox":(x1,y1,x2-x1,y2-y1)})
        i=j if j>i else i+1
    return lines

def score_name_line(line, imgW, imgH, card_band_xyxy=None, mode="balanced"):
    s=line["text"]; x,y,w,h=line["bbox"]; cy=y+h/2
    lower_bias=cy/imgH
    horiz_ar=w/max(1.0,h)
    alpha_ratio=sum(ch.isalpha() for ch in s)/max(1,len(s))
    near_bonus=0.0
    if card_band_xyxy is not None:
        _,by1,_,by2=card_band_xyxy
        d=max(0.0, y - by2)
        near_bonus=max(0.0, 1.0 - d/(0.25*imgH))
    score=0.0
    score+=min(horiz_ar,18.0)*0.6
    score+=lower_bias*2.0
    score+=near_bonus*2.0
    score+=alpha_ratio*1.0
    if mode=="loose": score+=0.8
    return score

def line_avg_conf(ocr_items, idxs):
    vals = [ocr_items[i][2] for i in idxs if 0 <= i < len(ocr_items)]
    return float(sum(vals)/max(1,len(vals)))

def name_roi_below_band(imgW, imgH, band_xywh):
    if band_xywh is None:
        return (0, int(imgH*0.55), imgW, int(imgH*0.40))
    x,y,w,h = band_xywh
    top = min(imgH-1, y + h + int(0.02*imgH))
    roi_h = int(0.30*imgH)
    return (0, top, imgW, min(roi_h, imgH - top))

def detect_names(ocr_items, imgW, imgH, card_band_xywh=None, mode="balanced",
                 name_conf=12.0, roi=None, hard_roi=None):
    lines=build_text_lines(ocr_items, imgW, imgH)
    if not lines: return []
    card_band_xyxy=None
    if card_band_xywh is not None:
        x,y,w,h=card_band_xywh; card_band_xyxy=(x,y,x+w,y+h)

    rx=ry=rw=rh=0
    if roi is not None: rx,ry,rw,rh=roi

    cand=[]
    for ln in lines:
        t=ln["text"].strip()
        x,y,w,h=ln["bbox"]
        if hard_roi is not None:
            hx,hy,hw,hh = hard_roi
            in_hard = (hx <= x and x+w <= hx+hw and hy <= y and y+h <= hy+hh)
            if not in_hard:
                continue
        is_top=(y+h/2) < (0.40*imgH)
        if is_top and is_brand_text(t):
            continue
        if not is_name_candidate(t):
            continue
        avgc = line_avg_conf(ocr_items, ln["idxs"])
        if (avgc < name_conf - 4) and (not any(ocr_items[i][2] >= name_conf for i in ln["idxs"])):
            continue
        ln["score"]=score_name_line(ln, imgW, imgH, card_band_xyxy, mode)
        in_roi = (rx <= x and x+w <= rx+rw and ry <= y and y+h <= ry+rh)
        if in_roi: ln["score"] += 2.0
        cand.append(ln)

    cand.sort(key=lambda x:x["score"], reverse=True)
    top=[(c["text"], c["idxs"]) for c in cand[:2]]
    if top: return top

    floor = int(imgH*0.55)
    cand_fb = []
    for ln in lines:
        t = ln["text"].strip()
        x,y,w,h = ln["bbox"]
        if (y + h/2) < floor: 
            continue
        if is_brand_text(t): 
            continue
        if not is_name_candidate(t):
            continue
        score = len(t.replace(" ","")) + (w/max(1,h))*0.15
        cand_fb.append((score, t, ln["idxs"]))
    cand_fb.sort(reverse=True)
    return [(cand_fb[0][1], cand_fb[0][2])] if cand_fb else []

def stitch_card_numbers(ocr_items):
    toks=[]
    for i,(box,text,conf) in enumerate(ocr_items):
        t=normalize_digitish(text); r=rect_from_box(box)
        if not r or not t: continue
        x,y,w,h=r; toks.append((i,x,y,w,h,t))
    toks.sort(key=lambda t:(t[2],t[1]))
    cands=[]; n=len(toks); i=0
    while i<n:
        idx_i,x,y,w,h,t=toks[i]
        if not (t.isdigit() and 3<=len(t)<=4): i+=1; continue
        seq=[(idx_i,t,(x,y,w,h))]; j=i+1
        while j<n:
            idx_j,xj,yj,wj,hj,tj=toks[j]
            if same_textline((x,y,w,h),(xj,yj,wj,hj)) and tj.isdigit() and (3<=len(tj)<=4):
                seq.append((idx_j,tj,(xj,yj,wj,hj))); j+=1
            else: break
        for s in range(0,len(seq)):
            for e in range(s+3, min(len(seq), s+5)+1):
                number=''.join(tok for _,tok,_ in seq[s:e])
                if 13<=len(number)<=19:
                    used=[idx for idx,_,_ in seq[s:e]]
                    cands.append((number, used))
        i=j if j>i else i+1
    uniq={}
    for num,idxs in cands: uniq.setdefault(num, idxs)
    return [(k, uniq[k]) for k in uniq]

def hamming(a,b): 
    if len(a)!=len(b): return 9999
    return sum(ch1!=ch2 for ch1,ch2 in zip(a,b))

def candidate_score(num, luhn_ok, avg_conf): 
    return (0 if luhn_ok else 1, -avg_conf, -len(num))

def dedupe_card_candidates(cands):
    groups=[]; used=[False]*len(cands)
    def overlap(a,b):
        sa,sb=set(a),set(b); inter=len(sa&sb)
        return inter/max(1,min(len(sa),len(sb)))
    for i in range(len(cands)):
        if used[i]: continue
        base=cands[i]; group=[i]; used[i]=True
        for j in range(i+1,len(cands)):
            if used[j]: continue
            cj=cands[j]
            if len(base["num"])==len(cj["num"]):
                if hamming(base["num"],cj["num"])<=2 or overlap(base["idxs"],cj["idxs"])>=0.6:
                    group.append(j); used[j]=True
        groups.append(group)
    result=[]
    for g in groups:
        best=min(g, key=lambda k: candidate_score(cands[k]["num"], cands[k]["luhn_ok"], cands[k]["avg_conf"]))
        result.append(cands[best])
    return result

def parse_abs_roi(s):
    try:
        x,y,w,h = [int(v.strip()) for v in s.split(",")]
        return (x,y,w,h)
    except:
        raise ValueError("--name_roi 형식: x,y,w,h (정수 px)")

def parse_rel_roi(s):
    try:
        l,t,r,b = [float(v.strip()) for v in s.split(",")]
        assert 0<=l<r<=1 and 0<=t<b<=1
        return (l,t,r,b)
    except:
        raise ValueError("--name_roi_rel 형식: l,t,r,b (0~1 비율)")

def run_once_image(img, east_model_path=None, lang_list=("eng",), digits_pass=True,
                   strong=True, upscale=1.4, conf_th=35.0, name_conf=12.0, relaxed=False,
                   blur_margin=8, blur_ksize=41, no_warp=False, use_emboss=False,
                   blur_all_text=False, draw_boxes=False, debug=False,
                   cardnum_pad=20, name_mode="balanced", blur_brands=False,
                   hard_roi=None, bottom_only=False, fast=False, max_side=1600):

    if img is None:
        raise RuntimeError("이미지 배열이 None 입니다")

    # resize to max_side (speed gain)
    H0, W0 = img.shape[:2]
    if max(H0, W0) > max_side:
        scale = max_side / float(max(H0, W0))
        img = cv2.resize(img, (int(W0*scale), int(H0*scale)), interpolation=cv2.INTER_AREA)

    # fast profile tweaks
    if fast:
        east_model_path = None
        use_emboss = False
        no_warp = True
        upscale = min(upscale, 1.3)

    img_proc = img if no_warp else perspective_fix(img)
    img_proc, gray = preprocess(img_proc, strong=strong, upscale=upscale, do_deskew=not fast)
    imgH, imgW = img_proc.shape[:2]

    boxes=[]
    if use_emboss:
        bin_for_digits=enhance_embossed_digits(gray)
        boxes+=digit_line_boxes_from_bin(bin_for_digits, min_area=600, min_ar=3.5)
    if east_model_path and os.path.exists(east_model_path):
        boxes+=east_boxes(img_proc, east_model_path, min_conf=0.5)

    if boxes:
        from imutils.object_detection import non_max_suppression
        boxes=non_max_suppression(np.array(boxes))
        boxes=[normalize_xyxy_or_xywh(tuple(map(int,b))) for b in boxes]
    else:
        boxes=[]

    langs_easy=_map_langs_for_easyocr(lang_list)
    reader=easyocr.Reader(langs_easy, gpu=False, verbose=False)

    canv = 1600 if fast else 2560
    magr = 1.5 if fast else 2.0

    rs_general = easyocr_items(reader, gray, allowlist=None,
                               canvas_size=canv, mag_ratio=magr, decoder='greedy')
    ocr_items = _to_items(rs_general, conf_th)

    name_gray = boost_name_contrast(gray)
    rs_name = easyocr_items(reader, name_gray, allowlist="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz '.-",
                            canvas_size=canv, mag_ratio=magr, decoder='greedy')
    ocr_items += _to_items(rs_name, name_conf)

    if debug: print(f"OCR 토큰 수: {len(ocr_items)}")

    raw_cands=[]
    for num,idxs in stitch_card_numbers(ocr_items):
        l_ok=luhn_check(num)
        if l_ok or (relaxed and len(num)==16):
            avg_conf=float(np.mean([ocr_items[idx][2] for idx in idxs])) if idxs else 0.0
            raw_cands.append({"num":num,"idxs":idxs,"avg_conf":avg_conf,"luhn_ok":bool(l_ok)})
    for k,(box,text,conf) in enumerate(ocr_items):
        clean=normalize_digitish(text)
        if 13<=len(clean)<=19:
            l_ok=luhn_check(clean)
            if l_ok or (relaxed and len(clean)==16):
                raw_cands.append({"num":clean,"idxs":[k],"avg_conf":float(conf),"luhn_ok":bool(l_ok)})

    if len(raw_cands) == 0:
        if debug: print("⛔ 카드번호 패턴 없음 → 종료")
        return {
            "image_redacted": img_proc,
            "card_numbers": [],
            "expiry": [],
            "names": [],
            "blur_boxes": []
        }

    uniq_cards=dedupe_card_candidates(raw_cands)
    found_cards=[c["num"] for c in uniq_cards]

    blur_rects=[]; found_expiry=[]; found_names=[]

    for (box,text,conf) in ocr_items:
        if not text: continue
        m=EXP_PAT.search(text)
        if m:
            found_expiry.append(m.group())
            r=rect_from_box(box)
            if r is not None:
                blur_rects.append(r)

    card_band_xywh=None
    if uniq_cards:
        rects=[]
        for idx in uniq_cards[0]["idxs"]:
            r=rect_from_box(ocr_items[idx][0])
            if r:
                x,y,w,h=r; rects.append((x,y,x+w,y+h))
        if rects:
            x1=min(r[0] for r in rects); y1=min(r[1] for r in rects)
            x2=max(r[2] for r in rects); y2=max(r[3] for r in rects)
            pad=int(cardnum_pad)
            x1=max(0,x1-pad); y1=max(0,y1-pad); x2=min(imgW,x2+pad); y2=min(imgH,y2+pad)
            card_band_xywh=(x1,y1,x2-x1,y2-y1)
            blur_rects.append(card_band_xywh)

    soft_roi = name_roi_below_band(imgW, imgH, card_band_xywh)

    if isinstance(hard_roi, tuple) and len(hard_roi)==5 and hard_roi[-1]=="REL":
        l,t,r,b,_ = hard_roi
        hard_roi = (int(l*imgW), int(t*imgH), int((r-l)*imgW), int((b-t)*imgH))
    if bottom_only and hard_roi is None:
        hard_roi = (0, int(imgH*0.50), imgW, int(imgH*0.50))

    for nm, idxs in detect_names(ocr_items, imgW, imgH, card_band_xywh, mode=name_mode,
                                 name_conf=name_conf, roi=soft_roi, hard_roi=hard_roi):
        found_names.append(nm)
        for idx in idxs:
            r=rect_from_box(ocr_items[idx][0])
            if r:
                txt=ocr_items[idx][1]
                if blur_brands or (not is_brand_text(txt)):
                    blur_rects.append(r)

    found_expiry=list(dict.fromkeys(found_expiry))
    found_names =list(dict.fromkeys(found_names))
    blur_rects  =uniq_rects_xywh(blur_rects)

    if (not blur_rects) and blur_all_text:
        for (box,text,conf) in ocr_items:
            r=rect_from_box(box)
            if r is not None:
                blur_rects.append(r)
        print(f"⚠️  강제 블러: OCR 토큰 {len(blur_rects)}개 블러 처리")

    if draw_boxes:
        dbg=img_proc.copy()
        for (bx,by,bw,bh) in blur_rects:
            cv2.rectangle(dbg,(bx,by),(bx+bw,by+bh),(0,0,255),2)
        cv2.imwrite("out_debug.jpg", dbg)
        print("🔴 디버그 박스 저장: out_debug.jpg")

    # 블러 적용 (단일 구현으로 통일)
    redacted=img_proc.copy()
    k=blur_ksize if blur_ksize%2==1 else blur_ksize+1
    for (bx,by,bw,bh) in blur_rects:
        X,Y,Ww,Hh=expand_rect((bx,by,bw,bh), blur_margin, imgW, imgH)
        roi=redacted[Y:Y+Hh, X:X+Ww]
        if roi.size>0:
            redacted[Y:Y+Hh, X:X+Ww]=cv2.GaussianBlur(roi,(k,k),0)

    return {
        "image_redacted": redacted,
        "card_numbers": [{"masked":mask_card_number(n), "brand":guess_brand(n), "luhn":luhn_check(n)} for n in found_cards],
        "expiry": found_expiry,
        "names": found_names,
        "blur_boxes": blur_rects
    }


def run_once(image_path, east_model_path=None, lang_list=("eng",), digits_pass=True,
             strong=True, upscale=1.4, conf_th=35.0, name_conf=12.0, relaxed=False,
             blur_margin=8, blur_ksize=41, no_warp=False, use_emboss=False,
             blur_all_text=False, draw_boxes=False, debug=False,
             cardnum_pad=20, name_mode="balanced", blur_brands=False,
             hard_roi=None, bottom_only=False, fast=False, max_side=1600):
    """
    중복 로직 제거: 이미지 경로를 열고 `run_once_image`에 위임합니다.
    CLI/호출 호환성을 위해 인자는 유지합니다.
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(image_path)
    img=cv2.imread(image_path)
    if img is None:
        raise RuntimeError("이미지를 열 수 없습니다")

    return run_once_image(
        img,
        east_model_path=east_model_path,
        lang_list=lang_list,
        digits_pass=digits_pass,
        strong=strong,
        upscale=upscale,
        conf_th=conf_th,
        name_conf=name_conf,
        relaxed=relaxed,
        blur_margin=blur_margin,
        blur_ksize=blur_ksize,
        no_warp=no_warp,
        use_emboss=use_emboss,
        blur_all_text=blur_all_text,
        draw_boxes=draw_boxes,
        debug=debug,
        cardnum_pad=cardnum_pad,
        name_mode=name_mode,
        blur_brands=blur_brands,
        hard_roi=hard_roi,
        bottom_only=bottom_only,
        fast=fast,
        max_side=max_side,
    )

# =================== Entry ===================
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--east", default="")
    ap.add_argument("--langs", default="eng")     # e.g., eng+kor
    ap.add_argument("--save", default="out_redacted.jpg")
    ap.add_argument("--no_warp", action="store_true")
    ap.add_argument("--strong", action="store_true")
    ap.add_argument("--upscale", type=float, default=1.9)
    ap.add_argument("--digits_pass", action="store_true")
    ap.add_argument("--conf", type=float, default=25.0)
    ap.add_argument("--name_conf", type=float, default=10.0)
    ap.add_argument("--relaxed", action="store_true")
    ap.add_argument("--blur_margin", type=int, default=16)
    ap.add_argument("--blur_ksize", type=int, default=61)
    ap.add_argument("--emboss", action="store_true")
    ap.add_argument("--blur_all_text", action="store_true")
    ap.add_argument("--draw_boxes", action="store_true")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--cardnum_pad", type=int, default=28)
    ap.add_argument("--name_mode", choices=["strict","balanced","loose"], default="loose")
    ap.add_argument("--blur_brands", action="store_true", help="브랜드 텍스트도 블러")
    # ROI controls
    ap.add_argument("--name_roi", default="", help="이름 강제 ROI (px: x,y,w,h)")
    ap.add_argument("--name_roi_rel", default="", help="이름 강제 ROI (비율: l,t,r,b)")
    ap.add_argument("--name_bottom_only", action="store_true", help="하단 50%에서만 이름 탐색")
    # FAST profile
    ap.add_argument("--fast", action="store_true", help="속도 우선 프로파일(2-pass OCR, no warp/EAST/emboss)")
    ap.add_argument("--max_side", type=int, default=1600, help="긴 변 리사이즈 상한(px)")

    args=ap.parse_args()

    # Prepare hard ROI argument
    hard_roi = None
    if args.name_roi:
        hard_roi = parse_abs_roi(args.name_roi)
    elif args.name_roi_rel:
        l,t,r,b = parse_rel_roi(args.name_roi_rel)
        hard_roi = (l,t,r,b,"REL")  # run_once에서 이미지 크기로 변환

    res=run_once(
        image_path=args.image,
        east_model_path=(args.east or None),
        lang_list=tuple(x.strip() for x in args.langs.split("+") if x.strip()),
        digits_pass=args.digits_pass,
        strong=args.strong,
        upscale=args.upscale,
        conf_th=args.conf,
        name_conf=args.name_conf,
        relaxed=args.relaxed,
        blur_margin=args.blur_margin,
        blur_ksize=args.blur_ksize,
        no_warp=args.no_warp,
        use_emboss=args.emboss,
        blur_all_text=args.blur_all_text,
        draw_boxes=args.draw_boxes,
        debug=args.debug,
        cardnum_pad=args.cardnum_pad,
        name_mode=args.name_mode,
        blur_brands=args.blur_brands,
        hard_roi=hard_roi,
        bottom_only=args.name_bottom_only,
        fast=args.fast,
        max_side=args.max_side
    )

    print("\n"+"="*60)
    print("🧾 신용카드 OCR 결과 (EasyOCR, {} mode)".format("FAST" if args.fast else "NORMAL"))
    print("="*60)
    if res["card_numbers"]:
        for i,cn in enumerate(res["card_numbers"],1):
            print(f"• 카드번호 {i}: {cn['masked']}  (Brand: {cn['brand']}, Luhn: {'OK' if cn['luhn'] else 'RELAXED'})")
    else:
        print("• 카드번호: (없음)")
    print("• 유효기간:", ", ".join(res["expiry"]) if res["expiry"] else "(없음)")
    print("• 이름:", "; ".join(['[마스킹됨] '+n for n in res['names']]) if res["names"] else "(없음)")
    print(f"• 블러 박스 수: {len(res['blur_boxes'])}")
    print("="*60)
    cv2.imwrite(args.save, res["image_redacted"])
    print(f"💾 저장: {args.save}\n")

if __name__=="__main__":
    main()
