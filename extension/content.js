console.log("🟢 content.js boot (auto-send text-only, no synthetic drag)");

let isForwarding = false;
let attachTimer = null;
let allowNativeSendOnce = false; // 다음 1회 네이티브 전송 허용한다.

window.__pendingUpload = null;
window.__lastFileInput = null;
window.__PII_SYNTHETIC_DROP__ = false;
window.__BLOCK_NATIVE_SEND__ = false;

const raf2 = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
const wait = (ms)=> new Promise(r=> setTimeout(r, ms));
const isVisible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects?.().length);

function getDeepActiveElement(root = document) {
  let a = root.activeElement || document.activeElement;
  while (a && a.shadowRoot && a.shadowRoot.activeElement) a = a.shadowRoot.activeElement;
  return a;
}
function pickInputNear(node) {
  if (!node) return null;
  if (node.tagName === 'TEXTAREA' && isVisible(node)) return node;
  if (node.isContentEditable && isVisible(node)) return node;
  const form = node.closest?.('form');
  if (form) {
    const ta = form.querySelector('textarea'); if (isVisible(ta)) return ta;
    const ce = form.querySelector('[role="textbox"][contenteditable="true"], div[contenteditable="true"]');
    if (isVisible(ce)) return ce;
  }
  const cands = [
    'textarea[aria-label][data-testid="prompt-textarea"]',
    'form textarea','textarea',
    '[role="textbox"][contenteditable="true"]','div[contenteditable="true"]'
  ];
  for (const sel of cands) { const el = (form || document).querySelector(sel); if (isVisible(el)) return el; }
  return null;
}
function findActiveInputField(){ return pickInputNear(getDeepActiveElement()) || pickInputNear(document.body); }
function readText(el){ if (!el) return ""; return el.tagName==="TEXTAREA" ? (el.value??"") : (el.innerText??el.textContent??""); }
function setInputValue(el, text) {
  if (!el) return false;
  if (el.tagName === "TEXTAREA") {
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set
                || Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), "value")?.set;
    setter ? setter.call(el, text) : (el.value = text);
    el.dispatchEvent(new Event("input",  { bubbles: true, composed: true }));
    el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
    return true;
  }
  if (el.isContentEditable) {
    el.textContent = text;
    el.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, data: text }));
    el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
    return true;
  }
  el.value = text;
  el.dispatchEvent(new Event("input",  { bubbles: true, composed: true }));
  el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
  return true;
}

function findSendButton() {
  const cands = [
    "button[data-testid='send-button']",
    "[data-testid='composer-send-button']",
    "form button[type='submit']",
    "button[aria-label*='Send']",
    "button[aria-label*='보내기']",
    "button:has(svg[aria-label='Send'])"
  ];
  for (const sel of cands) { const btn = document.querySelector(sel); if (btn) return btn; }
  return null;
}
function submitForm(inputEl){
  const form = inputEl?.closest?.('form');
  if (form && typeof form.requestSubmit === 'function') { try { form.requestSubmit(); return true; } catch {} }
  const btn = findSendButton();
  if (btn) { try {
      btn.dispatchEvent(new PointerEvent('pointerdown', {bubbles:true}));
      btn.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
      btn.dispatchEvent(new PointerEvent('pointerup', {bubbles:true}));
      btn.dispatchEvent(new MouseEvent('mouseup', {bubbles:true}));
      btn.click(); 
      return true; 
    } catch {} }
  try {
    const el = inputEl || document.activeElement || document.querySelector("textarea,[contenteditable='true']");
    el?.dispatchEvent(new KeyboardEvent('keydown', { key:'Enter', code:'Enter', keyCode:13, which:13, bubbles:true }));
    el?.dispatchEvent(new KeyboardEvent('keyup',   { key:'Enter', code:'Enter', keyCode:13, which:13, bubbles:true }));
    return true;
  } catch {}
  return false;
}

function collectAllRoots() {
  const roots = [document];
  const stack = [document];
  const seen = new Set();
  while (stack.length) {
    const root = stack.pop(); if (seen.has(root)) continue; seen.add(root);
    const all = (root.querySelectorAll ? root : document).querySelectorAll?.('*') || [];
    for (const el of all) if (el && el.shadowRoot) { roots.push(el.shadowRoot); stack.push(el.shadowRoot); }
  }
  return roots;
}
function qsAllDeep(selector) {
  const roots = collectAllRoots();
  const out = []; const seen = new Set();
  for (const r of roots) {
    try { const list = r.querySelectorAll(selector); for (const el of list) if (!seen.has(el)) { seen.add(el); out.push(el); } } catch {}
  }
  return out;
}
function robustClick(el) {
  try {
    el.dispatchEvent(new PointerEvent('pointerover', {bubbles:true}));
    el.dispatchEvent(new MouseEvent('mouseover', {bubbles:true}));
    el.dispatchEvent(new PointerEvent('pointerdown', {bubbles:true}));
    el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
    el.dispatchEvent(new PointerEvent('pointerup', {bubbles:true}));
    el.dispatchEvent(new MouseEvent('mouseup', {bubbles:true}));
    el.click(); return true;
  } catch { return false; }
}
function sendKey(el, key) {
  try { el.focus?.(); el.dispatchEvent(new KeyboardEvent('keydown', { key, code:key, bubbles:true })); el.dispatchEvent(new KeyboardEvent('keyup', { key, code:key, bubbles:true })); return true; } catch { return false; }
}

const ATTACHMENT_SEL = [
  "[data-testid*='attachment']",
  "[data-testid*='thumbnail']",
  "[data-testid*='file']",
  "[class*='attachment']",
  "[class*='Attachment']",
  "[class*='file-chip']",
  "[class*='FileChip']",
  "[class*='UploadPreview']",
  "figure img[alt]",
  "img[alt*='image'],img[alt*='preview']"
].join(",");

const REMOVE_BTN_SEL = [
  "button[aria-label*='삭제']","button[aria-label*='제거']","button[aria-label*='지우기']",
  "button[aria-label*='remove']","button[aria-label*='delete']","button[aria-label*='close']",
  "[data-testid*='remove']","[data-testid*='clear']",
  ".remove,.Remove,.close,.Close,.Delete",
  "svg[aria-label*='삭제'],svg[aria-label*='remove'],svg[aria-label*='delete']"
].join(",");

function countNativeAttachmentsDeep(){ return qsAllDeep(ATTACHMENT_SEL).length; }

function eventLooksLikeSend(e){
  const t = /** @type {HTMLElement} */(e.target);
  if (!t) return false;
  if (t.closest?.("button, [role='button']")) {
    const b = t.closest("button, [role='button']");
    const label = (b?.getAttribute?.('aria-label')||"").toLowerCase();
    if (label.includes('send') || label.includes('보내기')) return true;
    if (b?.querySelector?.("svg[aria-label*='Send']")) return true;
  }
  return false;
}
function blockNativeSend(enable){
  window.__BLOCK_NATIVE_SEND__ = !!enable;
}
function onSubmitCapture(e){
  if (!window.__BLOCK_NATIVE_SEND__) return;
  e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation?.();
}
function onKeyDownCapture(e){
  if (!window.__BLOCK_NATIVE_SEND__) return;
  const ke = /** @type {KeyboardEvent} */(e);
  const isEnter = ke.key === 'Enter' || ke.code === 'Enter';
  const withSendMod = (ke.metaKey || ke.ctrlKey) && isEnter;
  if (isEnter || withSendMod){
    e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation?.();
  }
}
function onClickCapture(e){
  if (!window.__BLOCK_NATIVE_SEND__) return;
  if (eventLooksLikeSend(e)){
    e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation?.();
  }
}
(function installBlockers(){
  document.addEventListener('submit', onSubmitCapture, true);
  document.addEventListener('keydown', onKeyDownCapture, true);
  document.addEventListener('click', onClickCapture, true);
})();

async function hardResetComposer() {
  const form = document.querySelector('form');
  if (form?.reset) { try { form.reset(); } catch {} }
  await raf2();

  const empty = new DataTransfer();
  qsAllDeep('input[type="file"]').forEach(inp => {
    try { inp.files = empty.files; } catch {}
    inp.value = '';
    inp.dispatchEvent(new Event('input',  { bubbles: true }));
    inp.dispatchEvent(new Event('change', { bubbles: true }));
  });

  qsAllDeep(REMOVE_BTN_SEL).forEach(robustClick);
  await wait(60);
  qsAllDeep(ATTACHMENT_SEL).forEach(ch => { sendKey(ch,'Delete'); sendKey(ch,'Backspace'); sendKey(ch,'Escape'); });
  await wait(80);

  const el = findActiveInputField();
  if (el) setInputValue(el, "");

  const t0 = performance.now();
  while (performance.now() - t0 < 1200) {
    const txt = (readText(findActiveInputField())||"").trim();
    if (txt === "" && countNativeAttachmentsDeep() === 0) break;
    await new Promise(r => requestAnimationFrame(r));
  }
}

function b64ToFile(base64, mime="image/png", name="masked.png"){
  const bin = atob(base64); const buf = new Uint8Array(bin.length);
  for (let i=0;i<bin.length;i++) buf[i] = bin.charCodeAt(i);
  return new File([new Blob([buf],{ type:mime })], name, { type:mime, lastModified: Date.now() });
}
function findClosestFileInput(baseEl) {
  const roots = collectAllRoots();
  if (baseEl?.closest) { const f = baseEl.closest('form'); if (f) { const inp = f.querySelector('input[type="file"]:not([disabled])'); if (inp) return inp; } }
  for (const r of roots) {
    const inp = r.querySelector?.('input[type="file"]:not([disabled])');
    if (inp) return inp;
  }
  return null;
}
async function injectViaNearestInput(file, baseEl) {
  const input = findClosestFileInput(baseEl);
  if (!input) return false;
  try { const empty = new DataTransfer(); input.files = empty.files; } catch {}
  input.value = '';
  const dt = new DataTransfer(); dt.items.add(file);
  try { input.files = dt.files; } catch { return false; }
  input.dispatchEvent(new Event('input',  { bubbles: true }));
  input.dispatchEvent(new Event('change', { bubbles: true }));
  await raf2();
  return true;
}
async function attachFileOnly(file) {
  let ok = await injectViaNearestInput(file, document.querySelector('form') || document.body);
  const t0 = performance.now();
  while (!ok && performance.now() - t0 < 800) {
    if (countNativeAttachmentsDeep() > 0) { ok = true; break; }
    await new Promise(r => requestAnimationFrame(r));
  }
  return ok;
}

(function shieldSyntheticDrags(){
  const stopIfSynthetic = (e) => {
    if (!e.isTrusted) {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation?.();
      try { if (e.dataTransfer) e.dataTransfer.dropEffect = 'none'; } catch {}
    }
  };
  ['dragenter','dragover','drop','dragstart','dragleave']
    .forEach(t => document.addEventListener(t, stopIfSynthetic, true));
})();

function isTextLikeFile(file) {
  const name = (file.name || "").toLowerCase();
  const type = (file.type || "").toLowerCase();
  return type.startsWith("text/") || type.includes("json") || /\.txt$|\.csv$|\.json$/.test(name);
}
function isImageFile(file) {
  const type = (file.type || "").toLowerCase();
  const name = (file.name || "").toLowerCase();
  return type.startsWith("image/") || /\.(png|jpe?g|webp)$/.test(name);
}
function readFileAsTextPreview(file, maxLen = 200) {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = (e) => resolve(String(e.target.result || "").slice(0, maxLen));
    reader.onerror = () => resolve("");
    reader.readAsText(file);
  });
}
function readImageAsDataURL(file) {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = (e) => resolve(e.target.result);
    reader.onerror = () => resolve("");
    reader.readAsDataURL(file);
  });
}

async function scanText(text){
  try {
    const r = await fetch("http://127.0.0.1:5000/api/scan", {
      method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify({ text })
    });
    if (!r.ok) throw new Error(`scan failed ${r.status}`);
    return r.json();
  } catch (e) {
    console.warn("[scanText] fallback to original due to error:", e);
    return null;
  }
}
async function sendFileToServer(file) {
  const fd = new FormData();
  fd.append("file", file, file.name);
  fd.append("langs","eng+kor"); fd.append("fast","1"); fd.append("max_side","1200");
  fd.append("relaxed","1"); fd.append("upscale","1.3"); fd.append("conf","25");
  fd.append("name_conf","8"); fd.append("name_mode","loose");
  fd.append("cardnum_pad","24"); fd.append("blur_margin","20"); fd.append("blur_ksize","61");
  fd.append("name_bottom_only","1"); fd.append("draw_boxes","0");

  const res = await fetch("http://127.0.0.1:5000/api/ocr-mask", { method:"POST", body:fd });
  if (!res.ok) {
    let detail=""; try{ const j=await res.json(); detail=j?.error || JSON.stringify(j);}catch{ detail=await res.text().catch(()=> "");}
    console.error("[ocr-mask] HTTP",res.status,detail);
    throw new Error("ocr-mask failed "+res.status+(detail?(" :: "+detail):""));
  }
  return res.json();
}

async function loadOverlayModule() {
  const rt = (globalThis.chrome && chrome.runtime && typeof chrome.runtime.getURL==="function" && chrome.runtime)
          || (globalThis.browser && browser.runtime && typeof browser.runtime.getURL==="function" && browser.runtime)
          || null;
  if (rt) {
    const url = (chrome?.runtime||browser?.runtime).getURL('ui/overlay.js');
    return import(/* @vite-ignore */ url);
  }
  const srcUrl = '/ui/overlay.js';
  const src = await fetch(srcUrl).then(r => r.text());
  const blob = new Blob([src], { type:'text/javascript' });
  const obj = URL.createObjectURL(blob);
  const mod = await import(/* @vite-ignore */ obj);
  URL.revokeObjectURL(obj);
  return mod;
}

async function preparePendingImageForOverlay() {
  const pending = window.__pendingUpload;
  if (!pending || !pending.file) return null;

  const origFile = pending.file;

  let result;
  try { result = await sendFileToServer(origFile); } catch(e){ console.warn("[pii-guard] ocr-mask failed:", e); return null; }
  if (!result?.ok || !result.masked_base64) return null;

  let dataURL = ""; try { dataURL = await readImageAsDataURL(origFile); } catch {}
  const [header, body] = String(dataURL || "").split(",");
  const orig_base64 = body || "";
  const orig_mime = (header && header.match(/^data:(.*?);base64$/)?.[1]) || origFile.type || "image/*";

  return {
    kind: "image",
    original: { base64: orig_base64, mime: orig_mime, fileName: origFile.name || "image.png" },
    redacted: { base64: result.masked_base64, mime: result.masked_mime || "image/png", fileName: result.masked_name || `masked_${origFile.name || "image.png"}` },
    _origFile: origFile
  };
}

// 메인 흐름
async function forwardSend(initialInputEl) {
  // 1) 네이티브 전송 전면 차단한다.
  blockNativeSend(true);
  allowNativeSendOnce = false;

  let inputEl = findActiveInputField() || initialInputEl;
  const originalText = (readText(inputEl) || "").trim();

  // 2) 전처리(텍스트 스캔 + 이미지 ocr)
  let textPayload=null, imagePayload=null;
  try {
    await Promise.all([
      (async()=>{ if (originalText) textPayload = await scanText(originalText); })(),
      (async()=>{ imagePayload = await preparePendingImageForOverlay(); })()
    ]);
  } catch (e) { console.warn("pre-send failed:", e); }

  // 3) 오버레이(텍스트/이미지 개별 선택)
  const { showCombinedOverlay } = await loadOverlayModule();
  const combinedChoice = await showCombinedOverlay({
    text: (textPayload || originalText) ? {
      original: textPayload?.original_text ?? originalText,
      redacted: textPayload?.redacted_text ?? originalText,
      entities: textPayload?.entities, types: textPayload?.types
    } : null,
    image: imagePayload // null이면 이미지 섹션 숨김
  });
  if (!combinedChoice) { blockNativeSend(false); return; }

  // 4) 하드 리셋으로 원본 완전 제거
  await hardResetComposer();

  // 5) 최종 텍스트/이미지 재구성 (비식별/원본 중 선택본만)
  const finalText =
    textPayload && combinedChoice?.text === 'redacted'
      ? (textPayload.redacted_text || originalText || "")
      : (originalText || "");

  // 텍스트 주입
  const el = findActiveInputField();
  setInputValue(el, finalText);

  // 이미지 주입
  let attachedAnyImage = false;
  if (combinedChoice?.image && imagePayload) {
    let fileToAttach = null;
    if (combinedChoice.image === 'redacted' && imagePayload.redacted?.base64) {
      fileToAttach = b64ToFile(imagePayload.redacted.base64, imagePayload.redacted.mime || "image/png", imagePayload.redacted.fileName || "masked.png");
    } else if (combinedChoice.image === 'original' && imagePayload._origFile) {
      fileToAttach = imagePayload._origFile;
    }
    if (fileToAttach) {
      const ok = await attachFileOnly(fileToAttach);
      attachedAnyImage = !!ok;
      if (!ok) console.warn("[pii-guard] attach failed after reset — sending text only if auto-send chosen");
    }
  }

  // 6) 자동 전송 정책
  const hasImage = attachedAnyImage || !!(combinedChoice?.image && imagePayload);
  if (hasImage) {
    // 이미지가 있으면 자동 전송 금지 — 사용자에게 맡김
    allowNativeSendOnce = true;
    blockNativeSend(false);
    return;
  } else {
    // 텍스트만 있으면 — 자동 전송
    isForwarding = true; 
    detachHandlers();

    // 자동 전송 직전에 전역 차단을 해제하고 1회 통과 허용한다.
    allowNativeSendOnce = true;
    blockNativeSend(false);

    try { 
      await raf2(); 
      const target = el || inputEl || findActiveInputField();
      let sent = submitForm(target);
      if (!sent) {
        await raf2();
        sent = submitForm(target);
      }
    } finally {
      window.__pendingUpload = null;
      setTimeout(()=>{
        allowNativeSendOnce = false; 
        isForwarding = false;
        attachHandlers();
      }, 200);
    }
  }
}

function onKeyDown(e){
  if (isForwarding) return;
  if (e.key==="Enter" && !e.shiftKey){
    if (allowNativeSendOnce) {
      // 이번 1회는 네이티브 전송 허용
      allowNativeSendOnce = false;
      return;
    }
    blockNativeSend(true);
    e.preventDefault(); e.stopPropagation();
    forwardSend(getDeepActiveElement());
  }
}
function onClickSend(e){
  if (isForwarding) return;
  if (allowNativeSendOnce) {
    allowNativeSendOnce = false;
    return;
  }
  blockNativeSend(true);
  e.preventDefault(); e.stopPropagation();
  forwardSend(getDeepActiveElement());
}
function attachHandlers(){
  if (attachTimer) clearTimeout(attachTimer);
  attachTimer = setTimeout(()=>{
    document.removeEventListener("keydown", onKeyDown, true);
    document.addEventListener("keydown", onKeyDown, true);
    const btn = findSendButton();
    if (btn){ btn.removeEventListener("click", onClickSend, true); btn.addEventListener("click", onClickSend, true); }
  }, 80);
}
function detachHandlers(){
  document.removeEventListener("keydown", onKeyDown, true);
  const btn = findSendButton(); if (btn) btn.removeEventListener("click", onClickSend, true);
}
(function observeUI(){
  const ob = new MutationObserver(()=> attachHandlers());
  ob.observe(document.documentElement, { childList:true, subtree:true });
  attachHandlers();
})();

/* ------------------------- upload capture only ------------------------- */
(function bindFileInputs(root=document){
  root.addEventListener("change", async (event) => {
    const el = event.target;
    if (!(el && el.matches && el.matches('input[type="file"]'))) return;
    window.__lastFileInput = el;
    const files = Array.from(el.files || []); if (!files.length) return;
    const f = files[0];
    if (isImageFile(f)) {
      window.__pendingUpload = { file:f, inputEl: el };
      console.log("[pii-guard] pending image saved:", f.name);
    } else if (isTextLikeFile(f)) {
      const preview = await readFileAsTextPreview(f); console.log("[pii-guard] text preview:", preview);
    }
  }, true);

  root.addEventListener("drop", async (event) => {
    if (window.__PII_SYNTHETIC_DROP__) return;
    const dt = event.dataTransfer; if (!dt || !dt.files || !dt.files.length) return;
    const f = dt.files[0];
    const target = (event.target && (event.target.closest?.('form') || document)) && (
      event.target.closest?.('form')?.querySelector('input[type="file"]') ||
      document.querySelector('input[type="file"]')
    );
    if (target) window.__lastFileInput = target;
    if (isImageFile(f)) {
      window.__pendingUpload = { file:f, inputEl: target || null };
      console.log("[pii-guard] pending image (drop) saved:", f.name);
    } else if (isTextLikeFile(f)) {
      const txt = await readFileAsTextPreview(f); console.log("[pii-guard] text preview (drop):", txt);
    }
  }, true);
})(document);

console.log("🟢 content.js initialized (text-only auto-send mode)");
