// content.js
console.log("🟢 content.js boot");

let isForwarding = false;

function findActiveInputField() {
  // 후보 셀렉터를 순서대로 시도
  const cands = [
    'textarea[aria-label][data-testid="prompt-textarea"]',
    'form textarea',
    "textarea:not([style*='display: none'])",
    '[role="textbox"][contenteditable="true"]',
    "div[contenteditable='true']",
  ];
  for (const sel of cands) {
    const el = document.querySelector(sel);
    if (el && el.offsetParent !== null && el.offsetHeight > 0) {
      console.log("🔎 input match:", sel);
      return el;
    }
  }
  console.warn("⚠️ input not found");
  return null;
}

function readText(el) {
  if (!el) return "";
  if (el.tagName === "TEXTAREA") return el.value ?? "";
  return el.innerText ?? el.textContent ?? "";
}

function findSendButton() {
  const cands = [
    "button[data-testid='send-button']",
    'form button[type="submit"]',
    'button[aria-label="Send prompt"]',
  ];
  for (const sel of cands) {
    const btn = document.querySelector(sel);
    if (btn) {
      console.log("🔎 send button match:", sel);
      return btn;
    }
  }
  console.warn("⚠️ send button not found");
  return null;
}

function forwardSend(inputEl) {
  const text = (readText(inputEl) || "").trim();
  console.log("🧪 text read:", JSON.stringify(text));
  if (!text) return;

  // ✅ 콘솔 출력
  console.log("[ChatGPT User Input]:", text);

  // ✅ 실제 전송
  const btn = findSendButton();
  if (!btn) return;

  // 재귀 방지: 잠시 리스너 해제 후 클릭
  detachHandlers();
  isForwarding = true;
  try {
    btn.click();
  } finally {
    setTimeout(() => {
      isForwarding = false;
      attachHandlers();
    }, 120);
  }
}

function onKeyDown(e) {
  if (isForwarding) return;
  if (e.key === "Enter" && !e.shiftKey) {
    const inputEl = findActiveInputField();
    if (!inputEl) return;
    e.preventDefault();
    e.stopPropagation();
    console.log("⌨️ Enter intercepted");
    forwardSend(inputEl);
  }
}

function onClickSend(e) {
  if (isForwarding) return;
  const inputEl = findActiveInputField();
  if (!inputEl) return;
  e.preventDefault();
  e.stopPropagation();
  console.log("🖱️ Send click intercepted");
  forwardSend(inputEl);
}

function attachHandlers() {
  console.log("🧩 attachHandlers()");
  document.removeEventListener("keydown", onKeyDown, true);
  document.addEventListener("keydown", onKeyDown, true);

  const btn = findSendButton();
  if (btn) {
    btn.removeEventListener("click", onClickSend, true);
    btn.addEventListener("click", onClickSend, true);
  }
}

function detachHandlers() {
  console.log("🧩 detachHandlers()");
  document.removeEventListener("keydown", onKeyDown, true);
  const btn = findSendButton();
  if (btn) btn.removeEventListener("click", onClickSend, true);
}

function observeUI() {
  console.log("👀 observeUI start");
  const ob = new MutationObserver(() => attachHandlers());
  ob.observe(document.documentElement, { childList: true, subtree: true });
  attachHandlers();
}

observeUI();
console.log("🟢 content.js initialized");
