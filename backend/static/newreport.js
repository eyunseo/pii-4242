document.addEventListener("DOMContentLoaded", async () => {
  // 화면의 유형 배지 → byType 계산
  const tags = Array.from(document.querySelectorAll(".tag")).map(el => el.textContent.trim()).filter(Boolean);
  const byType = {};
  for (const t of tags) byType[t] = (byType[t] || 0) + 1;

  // 비식별 데이터(전체)
  const redactedData = document.querySelector(".bubble.safe")?.textContent || "";

  // 영향도 맵
  const IMPACT_MAP = {
    "주민등록번호":"1등급","외국인등록번호":"1등급","여권번호":"1등급","운전면허번호":"1등급",
    "계좌번호":"1등급","카드번호":"1등급","카드 유효기간":"1등급",
    "이름":"2등급","생년월일":"2등급","성별":"2등급","전화번호":"2등급","이메일":"2등급","주소":"2등급","연락처":"2등급"
  };

  // 서버에 리포트 요청
  let res;
  try {
    const r = await fetch("/report/gpt", {
      method: "POST",
      headers: { "Content-Type":"application/json" },
      body: JSON.stringify({
        piiCount: Object.values(byType).reduce((a,b)=>a+b, 0),
        byType,
        examples: {},     // 필요시 예시 전달
        redactedData      // 비식별 데이터(전체)
      })
    });
    res = await r.json();
  } catch (e) {
    console.error("POST /report/gpt 실패:", e);
    renderSummary({ summary: "자동 분석에 실패했습니다. 탐지된 항목을 확인해 주세요." });
    // 폴백: 표가 비지 않게 최소 행 구성
    renderResultSummary({ findings: Object.entries(byType).map(([k,v]) => ({pii_type:k, count:v, example:`[${k}_1]`, evidence:""})) });
    renderFindings({ findings: Object.entries(byType).map(([k,v]) => ({pii_type:k, count:v, impact:"", recommendation:""})) });
    return;
  }

  const rep = res?.report || {};
  renderSummary(rep);
  renderResultSummary(rep);
  renderFindings(rep);

  /* ===== 렌더 ===== */

  function renderSummary(rep){
    const summaryEl = document.getElementById("risk-summary");
    const badgeWrap = document.getElementById("risk-badges");
    if (!summaryEl || !badgeWrap) return;

    // summary 우선, 위험도 표기 제거 (overall_risk 사용 안 함)
    const raw = (rep.summary || rep.combined_risk || "").trim() || "(요약 없음)";
    summaryEl.innerHTML = brify(raw); // \n → <br>
    badgeWrap.innerHTML = "";         // 뱃지(overall_risk) 표시 제거
  }

  // 결과 요약(HTML 헤더는 6칸이지만 reason 제거 요구에 맞춰 5칸만 채움)
  function renderResultSummary(rep){
    const table = document.getElementById("sum-table");
    const tbody = document.getElementById("sum-tbody");
    if (!table || !tbody) return;

    let rows = Array.isArray(rep.findings) ? rep.findings : [];
    if (!rows.length){
      const byT = {};
      for (const t of tags) byT[t] = (byT[t] || 0) + 1;
      rows = Object.entries(byT).map(([k,v]) => ({ pii_type:k, count:v, example:`[${k}_1]`, evidence:"" }));
    }

    tbody.innerHTML = "";
    for (const f of rows){
      const type  = String(f.pii_type || "");
      const cnt   = Number(f.count || 0);
      const ex    = String(f.example || `[${type}_1]`);
      const ev    = String(f.evidence || "");
      const impactGrade = IMPACT_MAP[type] || "2등급";

      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${escapeHtml(type)}</td>
        <td>${cnt}건</td>
        <td>${escapeHtml(ex)}</td>
        <td>${escapeHtml(impactGrade)}</td>
        <td>${escapeHtml(ev)}</td>
      `;
      tbody.appendChild(tr);
    }
    table.style.display = rows.length ? "table" : "none";
  }

  // 상세 설명(4컬럼: 유형/건수/위험 설명/권장조치)
  function renderFindings(rep){
    const table = document.getElementById("risk-table");
    const tbody = document.getElementById("risk-tbody");
    if (!table || !tbody) return;

    const rows = Array.isArray(rep.findings) && rep.findings.length ? rep.findings : [];
    tbody.innerHTML = "";

    for (const f of rows) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${escapeHtml(f.pii_type || "")}</td>
        <td>${Number(f.count || 0)}</td>
        <td>${escapeHtml(f.impact || "")}</td>
        <td>${escapeHtml(f.recommendation || "")}</td>
      `;
      tbody.appendChild(tr);
    }
    table.style.display = rows.length ? "table" : "none";
  }

  /* util */
  function escapeHtml(s){
    return String(s || "")
      .replaceAll("&","&amp;").replaceAll("<","&lt;")
      .replaceAll(">","&gt;").replaceAll('"',"&quot;")
      .replaceAll("'","&#39;");
  }
  function brify(s){
    return escapeHtml(String(s || "")).replace(/\r?\n/g, "<br>");
  }
});
