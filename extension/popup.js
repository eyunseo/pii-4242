const API = "http://127.0.0.1:5000";

function load() {
  chrome.runtime.sendMessage({ type: "GET_LAST_PROMPT" }, (res) => {
    document.getElementById("last").textContent = res?.payload || "(없음)";
  });
}
document.getElementById("refresh").addEventListener("click", load);
load();

