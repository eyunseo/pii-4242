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
  const cssURL  = (chrome?.runtime||browser?.runtime).getURL('ui/overlay.css');
  const htmlURL = (chrome?.runtime||browser?.runtime).getURL('ui/overlay.html');

  const style = document.createElement('link');
  style.rel = 'stylesheet'; style.href = cssURL;
  shadowRoot.appendChild(style);

  const html = await fetch(htmlURL).then(r=>r.text());
  const tpl = document.createElement('template');
  tpl.innerHTML = html;
  const node = tpl.content.getElementById('pii-guard-tpl').content.cloneNode(true);
  shadowRoot.appendChild(node);
}

function _mergeTypeLabels(oldText, addText) {
  const a = (oldText||"").trim();
  if (!a) return addText||"";
  if (!addText) return a;
  return `${a} · ${addText}`;
}

/**
 * @param {{ text: null | { original:string, redacted:string, entities?:any, types?:string[] },
 *           image: null | { kind:'image', types?:string[], original:{base64,mime,fileName}, redacted:{base64,mime,fileName} },
 *           files: null | Array<{ kind:'file', original_name:string, types?:string[], total_count?:number,
 *                                  preview:Array<any>, redacted:{base64:string,mime:string,fileName:string} }> }} param0
 * @returns {Promise<{text:('original'|'redacted'|null), image:('original'|'redacted'|null), files:null|('original'|'redacted')[] }>}
 */
export async function showCombinedOverlay({ text, image, files }) {
  const host = document.createElement('div');
  const shadow = host.attachShadow({mode:'open'});
  document.documentElement.appendChild(host);

  await loadHTMLAndCSS(shadow);

  const textCols   = shadow.getElementById('text-cols');
  const imageCols  = shadow.getElementById('image-cols');
  const fileCols   = shadow.getElementById('file-cols');
  const typesLabel = shadow.getElementById('types');
  const btnOriginal= shadow.getElementById('use-original');
  const btnRedacted= shadow.getElementById('use-redacted');
  const reportBtn  = shadow.getElementById('pii-report');
  const cancelBtn  = shadow.getElementById('pii-cancel');

  btnOriginal.style.display = 'none';
  btnRedacted.style.display = 'none';

  const footer = shadow.getElementById('actions') || shadow.querySelector('.actions') || shadow;
  const confirm = document.createElement('button');
  confirm.textContent = '확인 및 전송';
  confirm.className = 'btn btn-primary';
  footer.appendChild(confirm);

  const close = (val)=>{ host.remove(); resolve(val); };
  let resolve;
  const p = new Promise(r=>{ resolve=r; });

  cancelBtn.addEventListener('click', ()=> close(null));

  let textChoice = null;
  if (text) {
    textCols.style.display = 'grid';
    shadow.getElementById('orig').textContent     = text.original ?? '';
    shadow.getElementById('redacted').textContent = text.redacted ?? '';

    const textToggle = document.createElement('div');
    textToggle.className = 'toggle text-toggle';
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
    textToggle.addEventListener('change', ()=>{
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

  let imageChoice = null;
  if (image && image.original?.base64) {
    imageCols.style.display = 'grid';
    const origSrc   = `data:${image.original.mime||'image/*'};base64,${image.original.base64}`;
    const redacSrc  = image.redacted?.base64 ? `data:${image.redacted.mime||'image/png'};base64,${image.redacted.base64}` : '';

    if (origSrc)  shadow.getElementById('origImg').src   = origSrc;
    if (redacSrc) shadow.getElementById('maskedImg').src = redacSrc;

    const imgToggle = document.createElement('div');
    imgToggle.className = 'toggle image-toggle';
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
    imgToggle.addEventListener('change', ()=>{
      const v = imgToggle.querySelector('input[name="imageChoice"]:checked')?.value;
      imageChoice = (v === 'redacted') ? 'redacted' : 'original';
    });

    const it = Array.isArray(image.types) ? image.types : [];
    const prev = (typesLabel.textContent || '').trim();
    if (it.length) {
      typesLabel.textContent = prev ? `${prev} · 이미지 탐지: ${it.join(', ')}` : `이미지 탐지: ${it.join(', ')}`;
    }
  } else {
    imageCols.style.display = 'none';
  }

  let fileChoiceArray = null;
  if (Array.isArray(files) && files.length) {
    fileCols.style.display = 'grid';
    const file = files[0];
    const origList   = shadow.getElementById('fileOrigList');
    const maskedList = shadow.getElementById('fileMaskedList');

    const makeKV = (obj) => {
      if (obj == null) return '(null)';
      if (typeof obj === 'object') {
        const lines = [];
        if (Array.isArray(obj)) {
          obj.slice(0,5).forEach((v, i)=> lines.push(`- [${i}] ${JSON.stringify(v)}`));
        } else {
          Object.entries(obj).slice(0,5).forEach(([k,v])=> lines.push(`- ${k}: ${typeof v==='string'?v:JSON.stringify(v)}`));
        }
        return lines.join('\n');
      }
      return String(obj);
    };

    const origLines = [];
    const maskLines = [];
    (file.preview||[]).slice(0,5).forEach((p)=>{
      if (p.kind === 'csv_row') {
        origLines.push(`[row ${p.index}] ${makeKV(p.original)}`);
        maskLines.push(`[row ${p.index}] ${makeKV(p.masked)}`);
      } else if (p.kind === 'json_field') {
        origLines.push(`[${p.path}] ${makeKV(p.original)}`);
        maskLines.push(`[${p.path}] ${makeKV(p.masked)}`);
      } else if (p.kind === 'json_item' || p.kind === 'json_obj') {
        origLines.push(`[${p.index}] ${makeKV(p.original)}`);
        maskLines.push(`[${p.index}] ${makeKV(p.masked)}`);
      } else {
        origLines.push(makeKV(p.original));
        maskLines.push(makeKV(p.masked));
      }
    });
    origList.textContent   = origLines.join('\n');
    maskedList.textContent = maskLines.join('\n');

    const fileToggle = document.createElement('div');
    fileToggle.className = 'toggle file-toggle';
    fileToggle.innerHTML = `
      <label style="margin-right:12px;">
        <input type="radio" name="fileChoice0" value="original" checked> 파일 원본
      </label>
      <label ${file.redacted?.base64 ? '' : 'style="opacity:0.5;"'}>
        <input type="radio" name="fileChoice0" value="redacted" ${file.redacted?.base64 ? '' : 'disabled'}> 파일 비식별
      </label>
    `;
    fileCols.insertAdjacentElement('afterend', fileToggle);
    fileChoiceArray = ['original'];
    fileToggle.addEventListener('change', ()=>{
      const v = fileToggle.querySelector('input[name="fileChoice0"]:checked')?.value;
      fileChoiceArray[0] = (v === 'redacted') ? 'redacted' : 'original';
    });

    const add = [];
    if (Array.isArray(file.types) && file.types.length) add.push(`파일 탐지: ${file.types.join(', ')}`);
    if (typeof file.total_count === 'number') add.push(`총 탐지: ${file.total_count}건`);
    if (add.length) typesLabel.textContent = _mergeTypeLabels(typesLabel.textContent, add.join(' · '));
  } else {
    fileCols.style.display = 'none';
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

  confirm.addEventListener('click', ()=>{
    const res = {
      text:  text  ? (textChoice  || 'original') : null,
      image: image ? (imageChoice || 'original') : null,
      files: fileChoiceArray || null,
    };
    close(res);
  });

  try {
    if (text) {
      window.__piiReportContext = {
        original_text: text.original || "",
        redacted_text: text.redacted || "",
        types: Array.isArray(text.types) ? text.types : []
      };
    }
  } catch {}

  return p;
}
