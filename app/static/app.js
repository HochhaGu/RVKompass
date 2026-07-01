// Event-Delegation auf document, weil die Ergebnistabelle bei jeder Suche per HTMX
// neu ins DOM eingesetzt wird - direkt an Buttons gebundene Listener würden dabei verloren gehen.
document.addEventListener("click", function (event) {
  const copyBtn = event.target.closest(".copy-notation");
  if (copyBtn) {
    copyToClipboard(copyBtn.dataset.notation, copyBtn, "📋");
  }
  // .rvk-online-link braucht keinen Handler mehr - verlinkt direkt per Deep-Link
  // (#notation/<Notation>) auf die jeweilige Systemstelle in RVK-Online.
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
