// Event-Delegation auf document, weil die Vorschlagsliste bei jeder Suche per HTMX
// neu ins DOM eingesetzt wird - direkt an Elemente gebundene Listener würden dabei verloren gehen.
// Ohne JavaScript bleibt alles sichtbar und nutzbar (Bedeutungen sind dann alle eingeblendet).

document.addEventListener("click", function (event) {
  const copyBtn = event.target.closest(".copy-notation");
  if (copyBtn) {
    copyToClipboard(copyBtn.dataset.notation, copyBtn);
  }
  // .rvk-online-link braucht keinen Handler - verlinkt direkt per Deep-Link
  // (#notation/<Notation>) auf die jeweilige Systemstelle in RVK-Online.
});

// Bedeutungen ein- und ausblenden (Checkboxen .sense-toggle mit aria-controls)
document.addEventListener("change", function (event) {
  const toggle = event.target.closest(".sense-toggle");
  if (!toggle) {
    return;
  }
  const block = document.getElementById(toggle.getAttribute("aria-controls"));
  if (block) {
    block.hidden = !toggle.checked;
  }
});

// Nach einer Neusuche aus dem Erweiterungsbaum den Fokus auf denselben Begriff zurücksetzen,
// damit die Tastaturbedienung nicht am Seitenanfang neu beginnt.
let lastTreeValue = null;
document.addEventListener("change", function (event) {
  if (event.target.matches && event.target.matches('#expansion-tree input[name="begriff"]')) {
    lastTreeValue = event.target.value;
  }
});
document.addEventListener("htmx:afterSwap", function () {
  if (lastTreeValue === null) {
    return;
  }
  const boxes = document.querySelectorAll('#expansion-tree input[name="begriff"]');
  for (const box of boxes) {
    if (box.value === lastTreeValue) {
      box.focus();
      break;
    }
  }
  lastTreeValue = null;
});

function announce(text) {
  const region = document.getElementById("live-status");
  if (region) {
    region.textContent = "";
    // kurz verzögert, damit Screenreader die gleiche Meldung erneut vorlesen
    setTimeout(() => {
      region.textContent = text;
    }, 50);
  }
}

function copyToClipboard(text, el) {
  const original = el.textContent;
  navigator.clipboard
    .writeText(text)
    .then(() => {
      el.textContent = "kopiert";
      announce("Notation " + text + " kopiert");
      setTimeout(() => {
        el.textContent = original;
      }, 1200);
    })
    .catch(() => {
      announce("Kopieren nicht möglich – bitte die Notation markieren und kopieren");
    });
}
