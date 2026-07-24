/**
 * Dusky Template Generator — Background Script
 * Forwards auto-save CSS payloads from content.js directly to the Native Messaging Host.
 */

browser.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "SAVE_TEMPLATE") {
    browser.runtime.sendNativeMessage("dusky_template_generator", {
      type: "SAVE_TEMPLATE",
      domain: msg.domain,
      css: msg.css
    }).then(response => {
      sendResponse({ status: "ok", response });
    }).catch(err => {
      sendResponse({ status: "error", error: err.message });
    });
    return true; // Keep message channel open for async response
  }
});
