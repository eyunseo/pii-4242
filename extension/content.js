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

  // Other input types
  el.value = text;
  el.dispatchEvent(new Event("input",  { bubbles: true, composed: true }));
  el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
  return true;
}

// Paste fallback (when React overwrites the value)
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

// Wait 1–2 frames to ensure React state is applied
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

/* ========== Main workflow ========== */
async function forwardSend(initialInputEl) {
  // Re-find the input element right before submission
  let inputEl = findActiveInputField() || initialInputEl;
  const original = (readText(inputEl) || "").trim();
  if (!original) return;

  // 1) Server scan
  let payload;
  try { payload = await scanText(original); }
  catch(e){
    console.warn("scanText failed:", e);
    detachHandlers(); isForwarding = true;
    try { submitForm(inputEl); } finally { setTimeout(()=>{ isForwarding=false; attachHandlers(); },120); }
    return;
  }

  // 2) Overlay selection
  const { showOverlay } = await import(chrome.runtime.getURL('ui/overlay.js'));
  const choice = await showOverlay(payload); // 'original' | 'redacted'
  const finalText = (choice === 'redacted') ? (payload.redacted_text || original) : original;

  // 3) Apply value (synchronize with React state) — re-locate in case the DOM changes
  inputEl = findActiveInputField() || inputEl;
  let ok = setInputValue(inputEl, finalText);
  if (!ok || (readText(inputEl) || "") !== finalText) {
    // Paste fallback when native setter is blocked
    pasteFallback(inputEl, finalText);
  }

  // Allow time for React to apply its internal state
  await raf2();

  // 4) Submission
  detachHandlers();
  isForwarding = true;
  try { submitForm(inputEl); }
  finally { setTimeout(()=>{ isForwarding=false; attachHandlers(); }, 120); }
}

/* ========== Event binding ========== */
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

/* ========== [UPLOAD HOOKS v2] text & image detection (log-only) ========== */

// 1) Determine file type
function isTextLikeFile(file) {
  const name = (file.name || "").toLowerCase();
  const type = (file.type || "").toLowerCase();
  return (
    type.startsWith("text/") ||
    type.includes("json") ||
    name.endsWith(".txt") ||
    name.endsWith(".csv") ||
    name.endsWith(".json")
  );
}

function isImageFile(file) {
  const type = (file.type || "").toLowerCase();
  const name = (file.name || "").toLowerCase();
  // Check file extension as well in case MIME type is empty
  return (
    type.startsWith("image/") ||
    name.endsWith(".png") ||
    name.endsWith(".jpg") ||
    name.endsWith(".jpeg") ||
    name.endsWith(".webp")
  );
}

// 2) Preview reader
function readFileAsTextPreview(file, maxLen = 200) {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = (e) => {
      const txt = String(e.target.result || "");
      resolve(txt.slice(0, maxLen));
    };
    reader.onerror = () => resolve("");
    reader.readAsText(file);
  });
}

function readImageAsDataURL(file) {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = (e) => resolve(e.target.result); // base64 data URL
    reader.onerror = () => resolve("");
    reader.readAsDataURL(file);
  });
}

// (Optional) Example usage for sending file to server
async function sendFileToServer(file, meta = {}) {
  const fd = new FormData();
  fd.append("file", file, file.name);
  fd.append("meta", JSON.stringify(meta));
  // const res = await fetch("http://127.0.0.1:5000/analyze-file", { method: "POST", body: fd });
  // return res.json();
  return { ok: true, skipped: true, note: "demo (no server call)" };
}

// 3) Handle upload events
async function handleUploadedFile(file, source = "file-input") {
  const meta = { name: file.name, type: file.type, size: file.size, source, ts: Date.now() };

  if (isTextLikeFile(file)) {
    console.log("📎 [UPLOAD] text-like detected:", meta);
    const preview = await readFileAsTextPreview(file);
    console.log("📄 [UPLOAD] text preview:", preview);
    // (Optional) Server analysis
    // const result = await sendFileToServer(file, meta);
    // console.log("🔎 server result:", result);
    return;
  }

  if (isImageFile(file)) {
    console.log("🖼 [UPLOAD] image detected:", meta);
    const dataURL = await readImageAsDataURL(file);
    console.log("🧪 [UPLOAD] image dataURL preview:", (dataURL || "").slice(0, 100) + "...");
    // (Optional) Server OCR analysis
    // const result = await sendFileToServer(file, meta);
    // console.log("🔎 server result:", result);
    return;
  }

  // Other formats
  console.log("📦 [UPLOAD] other file detected:", meta);
  // (Optional) Send to server for parsing (PDF/DOCX, etc.)
  // const result = await sendFileToServer(file, meta);
  // console.log("🔎 server result:", result);
}

// 4) Intercept input[type=file] (prevent duplicate attachment)
function attachFileInputListener(root = document) {
  const inputs = root.querySelectorAll('input[type="file"]:not([data-upload-hooked="1"])');
  inputs.forEach((input) => {
    input.dataset.uploadHooked = "1";
    input.addEventListener(
      "change",
      async (event) => {
        const files = Array.from(event.target.files || []);
        if (!files.length) return;
        for (const f of files) {
          await handleUploadedFile(f, "file-input");
        }
      },
      { capture: true, passive: true } // Do not interrupt the default upload flow
    );
  });
}

// 5) Detect drag-and-drop (do not interrupt default upload flow)
function attachDragDropListener(root = document) {
  root.addEventListener(
    "drop",
    async (event) => {
      const dt = event.dataTransfer;
      if (!dt || !dt.files || !dt.files.length) return;
      const files = Array.from(dt.files);
      for (const f of files) {
        await handleUploadedFile(f, "drag-and-drop");
      }
    },
    { capture: true }
  );
}

// 6) Observe DOM changes to hook into dynamically created file inputs
(function observeUploadInputs() {
  const ob = new MutationObserver(() => attachFileInputListener(document));
  ob.observe(document.documentElement || document.body, { childList: true, subtree: true });
  attachFileInputListener(document);
  attachDragDropListener(document);
  console.log("🟢 upload hooks (text+image) installed");
})();

