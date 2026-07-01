// Event-Delegation auf document, weil die Ergebnistabelle bei jeder Suche per HTMX
// neu ins DOM eingesetzt wird - direkt an Buttons gebundene Listener würden dabei verloren gehen.
document.addEventListener("click", function (event) {
  const copyBtn = event.target.closest(".copy-notation");
  if (copyBtn) {
    copyToClipboard(copyBtn.dataset.notation, copyBtn, "📋");
    return;
  }

  const rvkLink = event.target.closest(".rvk-online-link");
  if (rvkLink) {
    // Notation kopieren, damit sie in RVK-Online direkt eingefügt werden kann - kein
    // verifiziertes Deep-Link-Schema zu einzelnen Notationen dort verfügbar (siehe Plan).
    navigator.clipboard.writeText(rvkLink.dataset.notation).catch(() => {});
    // Linkverhalten (target="_blank") läuft normal weiter, kein preventDefault nötig.
  }
});

function copyToClipboard(text, el, originalLabel) {
  navigator.clipboard
    .writeText(text)
    .then(() => {
      el.textContent = "✓";
      setTimeout(() => {
        el.textContent = originalLabel;
      }, 1200);
    })
    .catch(() => {});
}
