/**
 * Dusky Template Generator — Popup Controller (Clean Minimal Edition)
 */

document.addEventListener("DOMContentLoaded", () => {
  const domainBadge = document.getElementById("domain-badge");
  const statVars = document.getElementById("stat-vars");
  const statMapped = document.getElementById("stat-mapped");
  const cssPreview = document.getElementById("css-preview");
  const statusMsg = document.getElementById("status-msg");

  const btnScan = document.getElementById("btn-scan");
  const btnPicker = document.getElementById("btn-picker");
  const btnSave = document.getElementById("btn-save");
  const btnCopy = document.getElementById("btn-copy");

  let currentDomain = "";
  let pickerActive = false;

  function showStatus(text, type = "success") {
    statusMsg.className = `status-msg ${type}`;
    statusMsg.textContent = text;
    setTimeout(() => {
      statusMsg.className = "status-msg";
    }, 4000);
  }

  function getActiveTab() {
    return browser.tabs.query({ active: true, currentWindow: true }).then(tabs => tabs[0]);
  }

  function scanCurrentPage(autoScan = false) {
    getActiveTab().then(tab => {
      if (!tab) return;
      try {
        const url = new URL(tab.url);
        currentDomain = url.hostname.replace(/^www\./, "");
        domainBadge.textContent = currentDomain || "unknown";
      } catch (e) {
        domainBadge.textContent = "unknown";
      }

      browser.tabs.sendMessage(tab.id, { type: "SCAN_PAGE", autoScan }).then(res => {
        if (!res) return;
        currentDomain = res.domain || currentDomain;
        domainBadge.textContent = currentDomain;
        statVars.textContent = res.totalVars || 0;
        statMapped.textContent = res.mappedCount || 0;
        cssPreview.value = res.css || "";
        if (autoScan) {
          showStatus("✓ Auto-scanned site CSS variables!", "success");
        }
      }).catch(err => {
        browser.tabs.executeScript(tab.id, { file: "content.js" }).then(() => {
          setTimeout(() => scanCurrentPage(autoScan), 100);
        }).catch(() => {
          showStatus("Cannot inspect this page (restricted browser page).", "error");
        });
      });
    });
  }

  btnScan.addEventListener("click", () => {
    scanCurrentPage(true);
  });

  btnPicker.addEventListener("click", () => {
    getActiveTab().then(tab => {
      if (!tab) return;
      pickerActive = !pickerActive;
      btnPicker.classList.toggle("btn-active", pickerActive);
      browser.tabs.sendMessage(tab.id, { type: "TOGGLE_PICKER", enable: pickerActive }).then(() => {
        if (pickerActive) {
          showStatus("Click an element on the page to pick its role.", "success");
        }
      });
    });
  });

  btnSave.addEventListener("click", () => {
    const css = cssPreview.value.trim();
    if (!css) {
      showStatus("No CSS template to save!", "error");
      return;
    }

    if (!currentDomain) {
      showStatus("Unknown domain name!", "error");
      return;
    }

    btnSave.disabled = true;
    btnSave.textContent = "Saving...";

    browser.runtime.sendNativeMessage("dusky_template_generator", {
      type: "SAVE_TEMPLATE",
      domain: currentDomain,
      css: css
    }).then(response => {
      btnSave.disabled = false;
      btnSave.textContent = "💾 Save to ~/.config/dusky_sites/";
      if (response && response.status === "ok") {
        showStatus(`✓ ${response.message}`, "success");
      } else {
        showStatus(`Save failed: ${response?.error || "Unknown host error"}`, "error");
      }
    }).catch(err => {
      btnSave.disabled = false;
      btnSave.textContent = "💾 Save to ~/.config/dusky_sites/";
      showStatus(`Native host connection error: ${err.message}`, "error");
    });
  });

  btnCopy.addEventListener("click", () => {
    const css = cssPreview.value.trim();
    if (!css) {
      showStatus("Nothing to copy!", "error");
      return;
    }
    navigator.clipboard.writeText(css).then(() => {
      showStatus("✓ CSS copied to clipboard!", "success");
    }).catch(() => {
      showStatus("Failed to copy to clipboard", "error");
    });
  });

  // Initial page scan on popup open (loads existing custom rules ONLY without dumping 100s of auto-vars)
  scanCurrentPage(false);
});
