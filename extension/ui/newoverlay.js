(() => {
  const LOG_PREFIX = "[AnswerOverlay]";
  const DEBOUNCE_MS = 300;
  const AWAIT_TIMEOUT_MS = 20000;
  const AWAIT_INTERVAL_MS = 200;
  const ARM_COOLDOWN_MS = 2500;

  const raf2 = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));

  function findActiveInputField(){
    const a = document.activeElement;
    if (a && (a.tagName === "TEXTAREA" || a.isContentEditable)) return a;
    const cands = [
      'textarea[aria-label][data-testid="prompt-textarea"]',
      'form textarea','textarea',
      '[role="textbox"][contenteditable="true"]','div[contenteditable="true"]'
    ];
    for (const sel of cands) {
      const el = document.querySelector(sel);
      if (el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length)) return el;
    }
    return null;
  }
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

  async function loadNewHTML(shadowRoot) {
    const rt = (globalThis.chrome && chrome.runtime && chrome.runtime.getURL) || (globalThis.browser && browser.runtime && browser.runtime.getURL) || null;
    const cssURL  = rt ? (chrome?.runtime||browser?.runtime).getURL('ui/overlay.css')     : '/ui/overlay.css';
    const htmlURL = rt ? (chrome?.runtime||browser?.runtime).getURL('ui/newoverlay.html') : '/ui/newoverlay.html';

    const style = document.createElement('link');
    style.rel = 'stylesheet'; style.href = cssURL;
    shadowRoot.appendChild(style);

    const html = await fetch(htmlURL).then(r=>r.text());
    const tpl = document.createElement('template');
    tpl.innerHTML = html;
    const node = tpl.content.getElementById('gpt-answer-tpl') || tpl.content.getElementById('answer-overlay-tpl'); // 둘 중 어떤 id를 쓰든 대응
    shadowRoot.appendChild(node.content.cloneNode(true));
  }

  async function showAnswerOverlay({ prompt, answer, meta }) {
    const host = document.createElement('div');
    const shadow = host.attachShadow({mode:'open'});
    document.documentElement.appendChild(host);

    await loadNewHTML(shadow);

    const badges = shadow.getElementById('badges');
    const addBadge = (text)=>{
      if (!badges) return;
      const s = document.createElement('span');
      s.className = 'badge';
      s.textContent = `(${text})`;
      badges.appendChild(s);
    };

    // 배지(있으면)
    if (meta?.model) addBadge(`모델: ${meta.model}`);
    if (typeof meta?.latency_ms === 'number') addBadge(`지연: ${Math.round(meta.latency_ms)}ms`);
    if (meta?.tokens?.total || meta?.tokens?.prompt || meta?.tokens?.completion) {
      const t = meta.tokens || {};
      const total = t.total ?? ((t.prompt||0)+(t.completion||0));
      addBadge(`토큰: ${total}`);
    }

    // 본문
    const promptEl = shadow.getElementById('userPrompt') || shadow.getElementById('user-question');
    const answerEl = shadow.getElementById('modelAnswerLeft') || shadow.getElementById('modelAnswer') || shadow.getElementById('gpt-answer');
    if (promptEl) promptEl.textContent = String(prompt ?? '');
    if (answerEl) answerEl.textContent = String(answer ?? '');

    // 버튼
    const btnClose  = shadow.getElementById('ans-cancel') || shadow.getElementById('answer-close');
    const btnCopy   = shadow.getElementById('ans-copy');
    const btnInsert = shadow.getElementById('ans-insert');
    // (B) 리포트 버튼 핸들러
    const reportBtn = shadow.getElementById('ans-report');
    if (reportBtn) {
      reportBtn.addEventListener('click', () => {
        openNewReport({
          original_text: prompt ?? '',
          redacted_text: meta?.redacted_prompt ?? '',
          answer_text:   String(answer ?? ''),
          types:         meta?.types || []
        });
      });
    }



    return await new Promise((resolve)=> {
      btnClose?.addEventListener('click', ()=> { host.remove(); resolve('close'); });
      btnCopy?.addEventListener('click', async ()=>{
        try {
          await navigator.clipboard.writeText(String(answer ?? ''));
          if (btnCopy) { btnCopy.textContent = '복사됨'; setTimeout(()=> btnCopy.textContent = '복사', 1200); }
        } catch {
          if (btnCopy) { btnCopy.textContent = '복사 실패'; setTimeout(()=> btnCopy.textContent = '복사', 1200); }
        }
      });
      btnInsert?.addEventListener('click', ()=> { host.remove(); resolve('insert'); });
    });
  }

  let waitingForNextAssistant = false;
  let loggedForThisCycle = false;
  let lastLogged = "";
  let debounceTimer = null;
  let lastArmAt = 0;
  let targetAssistantTurn = null;
  let baseline = { count: 0, lastEl: null, lastLen: 0, ts: 0 };
  let lastPromptSnapshot = ""; // 전송 시점의 사용자 질문을 잡아두기

  const SELECTORS = {
    assistantTurn: '[data-testid="conversation-turn"][data-message-author-role="assistant"]',
    anyAssistant: '[data-message-author-role="assistant"]',
    body: '.markdown.prose, .prose, [data-testid="assistant-turn"]',
    sendButtons: [
      'button[data-testid="send-button"]',
      '[data-testid="send-button"] button',
      'button[aria-label="Send"]',
      'form button[type="submit"]'
    ],
    userTurn: '[data-testid="conversation-turn"][data-message-author-role="user"], [data-message-author-role="user"]'
  };

  const getAssistantBody = (turnEl) =>
    turnEl?.querySelector?.(SELECTORS.body) ||
    turnEl?.querySelector?.('[data-testid="assistant-turn"]') ||
    turnEl;

  const findAssistantTurns = () => {
    let nodes = document.querySelectorAll(SELECTORS.assistantTurn);
    if (nodes?.length) return Array.from(nodes);
    nodes = document.querySelectorAll(SELECTORS.anyAssistant);
    if (nodes?.length) return Array.from(nodes);
    const bodies = document.querySelectorAll(SELECTORS.body);
    return Array.from(bodies).map(
      (el) =>
        el.closest('[data-testid="conversation-turn"]') ||
        el.closest('[data-message-author-role]') ||
        el
    );
  };
  const getText = (el) => (el?.innerText || el?.textContent || "").trim();
  const getTextLen = (el) => getText(el).length;
  const awaitNonEmptyText = async (el) => {
    const start = Date.now();
    while (Date.now() - start < AWAIT_TIMEOUT_MS) {
      const t = getText(el);
      if (t.length > 0) return t;
      await new Promise((r) => setTimeout(r, AWAIT_INTERVAL_MS));
    }
    return "";
  };

  function snapshotBaseline(){
    const turns = findAssistantTurns();
    const count = turns.length;
    const last = count ? turns[count - 1] : null;
    const body = last ? getAssistantBody(last) : null;
    const len = body ? getTextLen(body) : 0;
    baseline = { count, lastEl: last, lastLen: len, ts: Date.now() };
    targetAssistantTurn = null;
  }

  function armOneShot(reason = "", { allowFallback = true } = {}) {
    const now = Date.now();
    if (waitingForNextAssistant && now - lastArmAt < ARM_COOLDOWN_MS) return;
    if (reason.startsWith("user-turn") && waitingForNextAssistant && !allowFallback) return;

    waitingForNextAssistant = true;
    loggedForThisCycle = false;
    lastLogged = "";
    lastArmAt = now;

    const el = findActiveInputField();
    lastPromptSnapshot = readText(el);

    snapshotBaseline();
  }

  async function onAnswerReady(fullText) {
    if (!waitingForNextAssistant || loggedForThisCycle) return;
    const normalized = (fullText || "").trim();
    if (!normalized || normalized === lastLogged) return;

    lastLogged = normalized;
    loggedForThisCycle = true;
    waitingForNextAssistant = false;
    targetAssistantTurn = null;

    const ctx = window.__piiReportContext || {};
    const meta = {
      redacted_prompt: ctx.redacted_text || "",
      types: Array.isArray(ctx.types) ? ctx.types : []
    };

    const action = await showAnswerOverlay({
      prompt: lastPromptSnapshot || "(질문)",
      answer: normalized,
      meta
    });

    if (action === 'insert') {
      const el = findActiveInputField();
      setInputValue(el, normalized);
      await raf2();
    }
  }

  function scheduleCheckAndLog(){
    if (!waitingForNextAssistant || loggedForThisCycle) return;
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(async () => {
      try {
        const turns = findAssistantTurns();
        const count = turns.length;

        if (targetAssistantTurn?.isConnected) {
          const body = getAssistantBody(targetAssistantTurn);
          const text = await awaitNonEmptyText(body);
          return onAnswerReady(text);
        }

        if (count > baseline.count) {
          targetAssistantTurn = turns[count - 1];
          const body = getAssistantBody(targetAssistantTurn);
          const text = await awaitNonEmptyText(body);
          return onAnswerReady(text);
        }

        if (count === baseline.count && baseline.lastEl) {
          const body = getAssistantBody(baseline.lastEl);
          if (body) {
            const len = getTextLen(body);
            const grown = len > baseline.lastLen;
            if (grown && Date.now() - baseline.ts >= 30) {
              targetAssistantTurn = baseline.lastEl;
              const text = await awaitNonEmptyText(body);
              return onAnswerReady(text);
            }
          }
        }
      } catch (e) {
        console.warn(LOG_PREFIX, "read error", e);
      }
    }, DEBOUNCE_MS);
  }

  function hookSendButtons(){
    const seen = new WeakSet();
    const attach = (btn) => {
      if (!btn || seen.has(btn)) return;
      seen.add(btn);
      btn.addEventListener("click", () => armOneShot("send-button click"), { capture: true });
    };
    const scan = () => {
      [
        'button[data-testid="send-button"]',
        '[data-testid="send-button"] button',
        'button[aria-label="Send"]',
        'form button[type="submit"]'
      ].forEach((sel) => {
        document.querySelectorAll(sel).forEach(attach);
      });
    };
    scan();
    setInterval(scan, 1500);
  }
  function hookKeydownSend(){
    document.addEventListener("keydown", (e)=>{
      const t = e.target;
      const isTextArea = t?.tagName === "TEXTAREA";
      const isCE = t?.getAttribute?.("contenteditable") === "true";
      if (!isTextArea && !isCE) return;

      const isPlainEnter =
        e.key === "Enter" &&
        !e.shiftKey && !e.ctrlKey && !e.altKey && !e.metaKey && !e.isComposing;

      if (isPlainEnter) armOneShot("keydown Enter");
    }, true);
  }

  const observer = new MutationObserver((mutations)=>{
    let assistantChanged = false;
    let userTurnAdded = false;

    for (const m of mutations) {
      if (m.addedNodes && m.addedNodes.length) {
        for (const n of m.addedNodes) {
          if (!(n instanceof HTMLElement)) continue;
          if (n.matches?.(SELECTORS.userTurn) || n.querySelector?.(SELECTORS.userTurn)) userTurnAdded = true;
          if (
            n.matches?.(SELECTORS.anyAssistant) ||
            n.querySelector?.(SELECTORS.anyAssistant) ||
            n.matches?.(SELECTORS.body) ||
            n.querySelector?.(SELECTORS.body)
          ) assistantChanged = true;
        }
      }
      if (m.type === "characterData") {
        const hostEl = m.target?.parentElement;
        if (hostEl?.closest?.(SELECTORS.anyAssistant) || hostEl?.closest?.(SELECTORS.body)) assistantChanged = true;
      }
    }

    if (userTurnAdded && !waitingForNextAssistant) {
      armOneShot("user-turn added (fallback)", { allowFallback: false });
    }
    if (assistantChanged) scheduleCheckAndLog();
  });

  observer.observe(document.documentElement || document.body, {
    childList: true, subtree: true, characterData: true
  });

  hookSendButtons();
  hookKeydownSend();


  if (typeof window !== 'undefined') {
    window.AnswerOverlay = Object.assign(window.AnswerOverlay || {}, { show: showAnswerOverlay });
  }

  function openNewReport({ original_text, redacted_text, answer_text, types }) {
    const BASE = "http://127.0.0.1:5000";
    const form = document.createElement("form");
    form.method = "POST";
    form.action = `${BASE}/report/preview_gpt`;
    form.target = "_blank";

    const add = (k, v) => {
      const i = document.createElement("input");
      i.type = "hidden"; i.name = k;
      i.value = (k === "types")
        ? JSON.stringify(Array.isArray(v) ? v : [])
        : String(v ?? "");

      form.appendChild(i);
    };

    add("original_text", original_text || "");
    add("redacted_text", redacted_text || "");   // ★ .bubble.safe 로 들어감 (필수)
    add("answer_text",   answer_text   || "");
    add("types",         types || []);           // ★ 건수/배지 계산에 필요

    document.body.appendChild(form);
    form.submit();
    form.remove();
  }

  // showAnswerOverlay(...) 내부 버튼 바인딩
  const reportBtn = shadow.getElementById("ans-report");
  if (reportBtn) {
    reportBtn.addEventListener("click", () => {
      const ctx = window.__piiReportContext || {};
      const effTypes = Array.isArray(meta?.types) ? meta.types :
                      (Array.isArray(ctx.types) ? ctx.types : []);
      openNewReport({
        original_text: (typeof prompt === "string" ? prompt : "") || "",
        redacted_text: (meta?.redacted_prompt ?? ctx.redacted_text ?? "") || "",
        answer_text:   String(answer ?? ""),
        types:         effTypes
      });
    });
  }



  btn.addEventListener("click", () => {
    window.open("/newreport", "_blank");
  });

  // 비식별 확정 직후(너의 기존 확정 지점에 추가)
  // 비식별 확정 직후(너의 기존 확정 지점에 추가)
  window.__piiReportContext = {
    original_text: text?.original || "",
    redacted_text: text?.redacted || "",
    types: Array.isArray(text?.types) ? text.types : []
  };

  console.log(LOG_PREFIX, "ready: arm on send; capture first assistant answer; show overlay.");
})();
