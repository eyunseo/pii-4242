console.log("🟢 content.js boot");

let isForwarding = false;
let attachTimer = null;

/* ========== Deep focus & input finding ========== */
function getDeepActiveElement(root = document) {
  let a = root.activeElement || document.activeElement;
  while (a && a.shadowRoot && a.shadowRoot.activeElement) {
    a = a.shadowRoot.activeElement;
  }
  return a;
}

function isVisible(el){ return el && el.offsetParent !== null && el.offsetHeight > 0; }

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
    'form textarea',
    'textarea',
    '[role="textbox"][contenteditable="true"]',
    'div[contenteditable="true"]',
  ];
  for (const sel of cands) {
    const el = (form || document).querySelector(sel);
    if (isVisible(el)) return el;
  }
  return null;
}

function findActiveInputField() {
  const deep = getDeepActiveElement();
  return pickInputNear(deep) || pickInputNear(document.body);
}

/* ========== React controlled input sync ========== */
function setInputValue(el, text) {
  if (!el) return false;

  // TEXTAREA: native setter -> input/change
  if (el.tagName === "TEXTAREA") {
    const setter =
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set ||
      Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), "value")?.set;
    setter ? setter.call(el, text) : (el.value = text);
    el.dispatchEvent(new Event("input",  { bubbles: true, composed: true }));
    el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
    return true;
  }

  // contenteditable
  if (el.isContentEditable) {
    el.textContent = text;
    el.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, data: text }));
    el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
    return true;
  }

  // 기타 input
  el.value = text;
  el.dispatchEvent(new Event("input",  { bubbles: true, composed: true }));
  el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
  return true;
}

// paste 폴백 (React가 value를 덮어쓸 때)
function pasteFallback(el, text) {
  try {
    el.focus();
    document.execCommand('selectAll', false, null);
    document.execCommand('insertText', false, text);
    el.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
    el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
    return true;
  } catch { return false; }
}

// React state가 실제로 반영되도록 1~2 프레임 대기
function raf2() {
  return new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
}

/* ========== DOM helpers ========== */
function readText(el) {
  if (!el) return "";
  if (el.tagName === "TEXTAREA") return el.value ?? "";
  return el.innerText ?? el.textContent ?? "";
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
  for (const sel of cands) {
    const btn = document.querySelector(sel);
    if (btn) return btn;
  }
  return null;
}

function submitForm(inputEl){
  const form = inputEl?.closest('form');
  if (form && typeof form.requestSubmit === 'function') { form.requestSubmit(); return true; }
  const btn = findSendButton();
  if (btn) { btn.click(); return true; }
  return false;
}

/* ========== API ========== */
async function scanText(text){
  const r = await fetch("http://127.0.0.1:5000/api/scan", {
    method:"POST",
    headers:{ "Content-Type":"application/json" },
    body: JSON.stringify({ text })
  });
  if (!r.ok) throw new Error(`scan failed ${r.status}`);
  return r.json(); // { ok, original_text, redacted_text, entities, types }
}

/* ========== Main flow ========== */
async function forwardSend(initialInputEl) {
  // 제출 직전 기준 입력 요소를 다시 찾음
  let inputEl = findActiveInputField() || initialInputEl;
  const original = (readText(inputEl) || "").trim();
  if (!original) return;

  // 1) 서버 스캔
  let payload;
  try { payload = await scanText(original); }
  catch(e){
    console.warn("scanText failed:", e);
    detachHandlers(); isForwarding = true;
    try { submitForm(inputEl); } finally { setTimeout(()=>{ isForwarding=false; attachHandlers(); },120); }
    return;
  }

  // 2) 오버레이 선택
  const { showOverlay } = await import(chrome.runtime.getURL('ui/overlay.js'));
  const choice = await showOverlay(payload); // 'original' | 'redacted'
  const finalText = (choice === 'redacted') ? (payload.redacted_text || original) : original;

  // 3) 값 반영 (React state 동기화) — DOM 교체 가능성 있어서 다시 찾아야 됨
  inputEl = findActiveInputField() || inputEl;
  let ok = setInputValue(inputEl, finalText);
  if (!ok || (readText(inputEl) || "") !== finalText) {
    // 네이티브 setter가 막힐 때 paste 폴백
    pasteFallback(inputEl, finalText);
  }

  // React가 내부 state를 적용할 시간 주는 설정
  await raf2();

  // 4) 제출
  detachHandlers();
  isForwarding = true;
  try { submitForm(inputEl); }
  finally { setTimeout(()=>{ isForwarding=false; attachHandlers(); }, 120); }
}

/* ========== Event wiring ========== */
function onKeyDown(e) {
  if (isForwarding) return;
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault(); e.stopPropagation();
    forwardSend(getDeepActiveElement());
  }
}

function onClickSend(e) {
  if (isForwarding) return;
  e.preventDefault(); e.stopPropagation();
  forwardSend(getDeepActiveElement());
}

function attachHandlers() {
  if (attachTimer) clearTimeout(attachTimer);
  attachTimer = setTimeout(() => {
    document.removeEventListener("keydown", onKeyDown, true);
    document.addEventListener("keydown", onKeyDown, true);

    const btn = findSendButton();
    if (btn) {
      btn.removeEventListener("click", onClickSend, true);
      btn.addEventListener("click", onClickSend, true);
    }
  }, 80);
}

function detachHandlers() {
  document.removeEventListener("keydown", onKeyDown, true);
  const btn = findSendButton();
  if (btn) btn.removeEventListener("click", onClickSend, true);
}

function observeUI() {
  const ob = new MutationObserver(() => attachHandlers());
  ob.observe(document.documentElement, { childList: true, subtree: true });
  attachHandlers();
}

observeUI();
console.log("🟢 content.js initialized");
