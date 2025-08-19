document.addEventListener("DOMContentLoaded", async () => {
  // 템플릿에 있는 .tag로 byType 만들어서 호출
  const tags = Array.from(document.querySelectorAll(".tag")).map(el => el.textContent.trim()).filter(Boolean);
  const byType = {};
  for (const t of tags) byType[t] = (byType[t] || 0) + 1;

  const payload = {
    piiCount: Object.values(byType).reduce((a,b)=>a+b, 0),
    byType,
    examples: {}
  };

  let res;
  try {
    res = await fetch("/report/gpt", {
      method: "POST",
      headers: { "Content-Type":"application/json" },
      body: JSON.stringify(payload)
    }).then(r => r.json());
  } catch (e) {
    console.error("POST /report/gpt 실패:", e);
    return;
  }

  const rep = res?.report || {};
  const summaryEl = document.getElementById("risk-summary");
  const badgeWrap = document.getElementById("risk-badges");
  const table = document.getElementById("risk-table");
  const tbody = document.getElementById("risk-tbody");

  if (!summaryEl || !badgeWrap || !table || !tbody) return;

  summaryEl.textContent = (rep.summary || "").trim() || "(요약 없음)";
  badgeWrap.innerHTML = "";
  if (rep.overall_risk) {
    const b = document.createElement("span");
    b.className = "badge risk-" + String(rep.overall_risk).toLowerCase();
    b.textContent = "Risk: " + rep.overall_risk;
    badgeWrap.appendChild(b);
  }

  tbody.innerHTML = "";
  const rows = Array.isArray(rep.findings) ? rep.findings : [];
  for (const f of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${f.pii_type || ""}</td>
      <td>${f.count || 0}</td>
      <td>${f.impact || ""}</td>
      <td>${f.likelihood || ""}</td>
      <td>${f.recommendation || ""}</td>
    `;
    tbody.appendChild(tr);
  }
  table.style.display = rows.length ? "table" : "none";
});
