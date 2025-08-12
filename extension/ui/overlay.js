// extension/ui/overlay.js
export async function showOverlay(payload) {
  // 1) Shadow DOM 컨테이너
  const host = document.createElement('div');
  const shadow = host.attachShadow({mode:'open'});
  document.documentElement.appendChild(host);

  // 2) CSS 로드
  const cssURL = chrome.runtime.getURL('ui/overlay.css');
  const style = document.createElement('link');
  style.rel = 'stylesheet'; style.href = cssURL;
  shadow.appendChild(style);

  // 3) 템플릿 로드 & 복제 (innerHTML 미사용)
  const htmlURL = chrome.runtime.getURL('ui/overlay.html');
  const html = await fetch(htmlURL).then(r=>r.text());
  const tpl = document.createElement('template');
  tpl.innerHTML = html;                    // 외부 파일만 파싱 1회
  const node = tpl.content.getElementById('pii-guard-tpl').content.cloneNode(true);
  shadow.appendChild(node);

  // 4) 데이터 바인딩 (textContent로만)
  shadow.getElementById('types').textContent =
    payload.types?.length ? `탐지된 종류: ${payload.types.join(', ')}` : '탐지 없음';
  shadow.getElementById('orig').textContent     = payload.original_text ?? '';
  shadow.getElementById('redacted').textContent = payload.redacted_text ?? '';

  // 5) 버튼 처리 → 선택 반환
  return new Promise((resolve)=>{
    const close = (val)=>{ host.remove(); resolve(val); };
    shadow.getElementById('use-original').addEventListener('click', ()=>close('original'));
    shadow.getElementById('use-redacted').addEventListener('click', ()=>close('redacted'));
  });
}
