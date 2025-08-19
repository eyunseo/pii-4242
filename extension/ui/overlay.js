export function openReport(payload){
  const f = document.createElement("form");
  f.method = "POST";
  f.action = "http://127.0.0.1:5000/report/preview";
  f.target = "_blank";
  const kv = {
    original_text: payload.original_text || "",
    redacted_text: payload.redacted_text || "",
    types: JSON.stringify(payload.types || []),
  };
  Object.entries(kv).forEach(([k,v])=>{
    const i=document.createElement("input"); i.type="hidden"; i.name=k; i.value=v;
    f.appendChild(i);
  });
  document.body.appendChild(f); f.submit(); f.remove();
}

async function loadHTMLAndCSS(shadowRoot) {
  const cssURL  = chrome.runtime.getURL('ui/overlay.css');
  const htmlURL = chrome.runtime.getURL('ui/overlay.html');

  const style = document.createElement('link');
  style.rel = 'stylesheet'; style.href = cssURL;
  shadowRoot.appendChild(style);

  const html = await fetch(htmlURL).then(r=>r.text());
  const tpl = document.createElement('template');
  tpl.innerHTML = html;
  const node = tpl.content.getElementById('pii-guard-tpl').content.cloneNode(true);
  shadowRoot.appendChild(node);
}

export async function showOverlay(payload) {
  const host = document.createElement('div');
  const shadow = host.attachShadow({mode:'open'});
  document.documentElement.appendChild(host);

  await loadHTMLAndCSS(shadow);

  shadow.getElementById('types').textContent =
    payload.types?.length ? `탐지된 종류: ${payload.types.join(', ')}` : '탐지 없음';

  const textCols   = shadow.getElementById('text-cols');
  const imageCols  = shadow.getElementById('image-cols');

  if (payload.kind === 'image') {
    textCols.style.display  = 'none';
    imageCols.style.display = 'grid';
    const { orig_base64, masked_base64 } = payload;
    if (orig_base64)   shadow.getElementById('origImg').src   = `data:image/*;base64,${orig_base64}`;
    if (masked_base64) shadow.getElementById('maskedImg').src = `data:${payload.masked_mime||'image/png'};base64,${masked_base64}`;
  } else {
    shadow.getElementById('orig').textContent     = payload.original_text ?? '';
    shadow.getElementById('redacted').textContent = payload.redacted_text ?? '';
  }

  return new Promise((resolve)=>{
    const close = (val)=>{ host.remove(); resolve(val); };
    const reportBtn = shadow.getElementById('pii-report');
    if (reportBtn) reportBtn.onclick = () => openReport(payload);

    shadow.getElementById('use-original').addEventListener('click', ()=>close('original'));
    shadow.getElementById('use-redacted').addEventListener('click', ()=>close('redacted'));
  });
}

/**
 * @param {{ text: null | { original:string, redacted:string, entities?:any, types?:string[] },
 *           image: null | { kind:'image', types?:string[], original:{base64,mime,fileName}, redacted:{base64,mime,fileName}, _inject?:any } }} param0
 * @returns {Promise<{text:('original'|'redacted'|null), image:('original'|'redacted'|null)}>}
 */
export async function showCombinedOverlay({ text, image }) {
  const host = document.createElement('div');
  const shadow = host.attachShadow({mode:'open'});
  document.documentElement.appendChild(host);

  await loadHTMLAndCSS(shadow);

  const textCols   = shadow.getElementById('text-cols');
  const imageCols  = shadow.getElementById('image-cols');
  const typesLabel = shadow.getElementById('types');
  const btnOriginal = shadow.getElementById('use-original');
  const btnRedacted = shadow.getElementById('use-redacted');
  const reportBtn   = shadow.getElementById('pii-report');

  btnOriginal.style.display = 'none';
  btnRedacted.style.display = 'none';

  const footer = shadow.getElementById('actions') || shadow.querySelector('.actions') || shadow;
  const confirm = document.createElement('button');
  confirm.textContent = '확인 및 전송';
  confirm.className = 'btn btn-primary';
  footer.appendChild(confirm);

  let textChoice = null;
  if (text) {
    textCols.style.display = 'grid';
    shadow.getElementById('orig').textContent     = text.original ?? '';
    shadow.getElementById('redacted').textContent = text.redacted ?? '';

    const textToggle = document.createElement('div');
    textToggle.className = 'toggle text-toggle';
    textToggle.style.margin = '8px 0';
    textToggle.innerHTML = `
      <label style="margin-right:12px;">
        <input type="radio" name="textChoice" value="original" checked> 텍스트 원본
      </label>
      <label>
        <input type="radio" name="textChoice" value="redacted"> 텍스트 비식별
      </label>
    `;
    textCols.insertAdjacentElement('afterend', textToggle);
    textChoice = 'original';
    textToggle.addEventListener('change', (e)=>{
      const v = textToggle.querySelector('input[name="textChoice"]:checked')?.value;
      textChoice = (v === 'redacted') ? 'redacted' : 'original';
    });

    const t = Array.isArray(text.types) ? text.types : [];
    if (t.length) {
      typesLabel.textContent = `텍스트 탐지: ${t.join(', ')}`;
    }
  } else {
    textCols.style.display = 'none';
  }

  let imageChoice = null; // 'original' | 'redacted' | null
  if (image && image.original?.base64) {
    imageCols.style.display = 'grid';
    const origSrc   = `data:${image.original.mime||'image/*'};base64,${image.original.base64}`;
    const redacSrc  = image.redacted?.base64 ? `data:${image.redacted.mime||'image/png'};base64,${image.redacted.base64}` : '';

    if (origSrc)  shadow.getElementById('origImg').src   = origSrc;
    if (redacSrc) shadow.getElementById('maskedImg').src = redacSrc;

    // 이미지 토글 UI
    const imgToggle = document.createElement('div');
    imgToggle.className = 'toggle image-toggle';
    imgToggle.style.margin = '8px 0';
    imgToggle.innerHTML = `
      <label style="margin-right:12px;">
        <input type="radio" name="imageChoice" value="original" checked> 이미지 원본
      </label>
      <label ${image.redacted?.base64 ? '' : 'style="opacity:0.5;"'}>
        <input type="radio" name="imageChoice" value="redacted" ${image.redacted?.base64 ? '' : 'disabled'}> 이미지 비식별
      </label>
    `;
    imageCols.insertAdjacentElement('afterend', imgToggle);
    imageChoice = 'original';
    imgToggle.addEventListener('change', (e)=>{
      const v = imgToggle.querySelector('input[name="imageChoice"]:checked')?.value;
      imageChoice = (v === 'redacted') ? 'redacted' : 'original';
    });
    const it = Array.isArray(image.types) ? image.types : [];
    const prev = (typesLabel.textContent || '').trim();
    if (it.length) {
      typesLabel.textContent = prev
        ? `${prev} · 이미지 탐지: ${it.join(', ')}`
        : `이미지 탐지: ${it.join(', ')}`;
    }
  } else {
    imageCols.style.display = 'none';
  }

  if (text) {
    reportBtn.onclick = () => openReport({
      original_text: text.original ?? '',
      redacted_text: text.redacted ?? '',
      types: text.types ?? []
    });
  } else {
    reportBtn.style.display = 'none';
  }

  // 닫기 & 결과 반환
  return new Promise((resolve)=>{
    const close = (val)=>{ host.remove(); resolve(val); };
    confirm.addEventListener('click', ()=>{
      // 텍스트/이미지 섹션이 없으면 null 유지
      const res = {
        text:  text  ? (textChoice  || 'original') : null,
        image: image ? (imageChoice || 'original') : null,
      };
      close(res);
    });
  });
}
