let lastPrompt = "";

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "PROMPT_CAPTURED") {
    console.log("Captured prompt:", message.payload);
    lastPrompt = message.payload;
  }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "GET_LAST_PROMPT") {
    sendResponse({ payload: lastPrompt });
  }
});
