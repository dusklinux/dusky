/**
 * Dusky Template Generator — Background Script
 * Forwards auto-save and delete CSS payloads from content.js directly to the Native Messaging Host.
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
    return true;
  } else if (msg.type === "DELETE_TEMPLATE") {
    browser.runtime.sendNativeMessage("dusky_template_generator", {
      type: "DELETE_TEMPLATE",
      domain: msg.domain
    }).then(response => {
      sendResponse({ status: "ok", response });
    }).catch(err => {
      sendResponse({ status: "error", error: err.message });
    });
    return true;
  }
});
