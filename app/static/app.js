// Event-Delegation auf document, weil Vorschläge, Chips und Facette bei jeder Suche per HTMX
// neu ins DOM eingesetzt werden - direkt an Elemente gebundene Listener gingen dabei verloren.
// Ohne JavaScript bleibt alles sichtbar und nutzbar: Facette offen, alle Bedeutungen
// untereinander, Verweise und Beispielsuchen als normale Links (/?q=…).

document.documentElement.classList.add("js");

function searchFor(query) {
  const input = document.getElementById("q");
  const form = document.getElementById("search-form");
  if (!input || !form) {
    return false;
  }
  input.value = query;
  if (typeof form.requestSubmit === "function") {
    form.requestSubmit();
  } else {
    form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
  }
  input.focus();
  return true;
}

document.addEventListener("click", function (event) {
  const copyBtn = event.target.closest(".copy-notation");
  if (copyBtn) {
    copyToClipboard(copyBtn.dataset.notation, copyBtn);
    return;
  }
  // Verweise („→ ST 301 anzeigen“) und Beispielsuchen: Suche im Formular statt Seitenwechsel
  const searchLink = event.target.closest(".notation-link, .example-search");
  if (searchLink && searchLink.dataset.query) {
    if (searchFor(searchLink.dataset.query)) {
      event.preventDefault();
    }
    return;
  }
  const facetToggle = event.target.closest(".facet-toggle");
  if (facetToggle) {
    const facet = document.getElementById("facet");
    const open = facetToggle.getAttribute("aria-expanded") !== "true";
    facetToggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (facet) {
      facet.classList.toggle("is-open", open);
    }
    return;
  }
  const tab = event.target.closest('[role="tab"]');
  if (tab) {
    selectTab(tab, true);
  }
  // .rvk-online-link braucht keinen Handler - verlinkt direkt per Deep-Link
  // (#notation/<Notation>) auf die jeweilige Systemstelle in RVK-Online.
});

// --- Bedeutungs-Tabs (ARIA-Tabs-Muster) ---------------------------------------------------

function tabsOf(tab) {
  const list = tab.closest('[role="tablist"]');
  return list ? Array.from(list.querySelectorAll('[role="tab"]')) : [tab];
}

function selectTab(tab, focus) {
  for (const other of tabsOf(tab)) {
    const selected = other === tab;
    other.setAttribute("aria-selected", selected ? "true" : "false");
    other.tabIndex = selected ? 0 : -1;
    const panel = document.getElementById(other.getAttribute("aria-controls"));
    if (panel) {
      panel.hidden = !selected;
    }
  }
  if (focus) {
    tab.focus();
  }
}

function initTabs(root) {
  for (const list of root.querySelectorAll('.sense-tabs[role="tablist"]')) {
    if (list.dataset.ready) {
      continue;
    }
    list.dataset.ready = "1";
    list.hidden = false;
    let chosen = null;
    for (const tab of list.querySelectorAll('[role="tab"]')) {
      const panel = document.getElementById(tab.getAttribute("aria-controls"));
      if (panel) {
        panel.setAttribute("role", "tabpanel");
        panel.setAttribute("aria-labelledby", tab.id);
        panel.tabIndex = 0;
      }
      if (tab.getAttribute("aria-selected") === "true") {
        chosen = tab;
      }
    }
    const tabs = list.querySelectorAll('[role="tab"]');
    if (tabs.length) {
      selectTab(chosen || tabs[0], false);
    }
  }
}

document.addEventListener("keydown", function (event) {
  const tab = event.target.closest && event.target.closest('[role="tab"]');
  if (!tab) {
    return;
  }
  const tabs = tabsOf(tab);
  const index = tabs.indexOf(tab);
  let next = null;
  if (event.key === "ArrowRight") {
    next = tabs[(index + 1) % tabs.length];
  } else if (event.key === "ArrowLeft") {
    next = tabs[(index - 1 + tabs.length) % tabs.length];
  } else if (event.key === "Home") {
    next = tabs[0];
  } else if (event.key === "End") {
    next = tabs[tabs.length - 1];
  }
  if (next) {
    event.preventDefault();
    selectTab(next, true);
  }
});

// --- Fokus nach einer Neusuche aus Chips oder Facette zurückgeben -----------------------------
// Damit die Tastaturbedienung nicht am Seitenanfang neu beginnt.

let pendingFocus = null;

document.addEventListener("click", function (event) {
  const remove = event.target.closest(".chip-remove");
  if (remove) {
    const all = Array.from(document.querySelectorAll("#chips .chip-remove"));
    pendingFocus = { kind: "remove", key: remove.dataset.key, index: all.indexOf(remove) };
    return;
  }
  const restore = event.target.closest(".chip-restore");
  if (restore) {
    pendingFocus = { kind: "restore", key: restore.dataset.key };
  }
});

document.addEventListener("change", function (event) {
  const box = event.target;
  if (box.matches && box.matches('#facet input[name="hg"]')) {
    pendingFocus = { kind: "facet", value: box.value };
  }
});

function restoreFocus() {
  if (!pendingFocus) {
    return;
  }
  const wanted = pendingFocus;
  pendingFocus = null;
  let target = null;
  if (wanted.kind === "facet") {
    target = Array.from(document.querySelectorAll('#facet input[name="hg"]')).find(
      (box) => box.value === wanted.value
    );
  } else if (wanted.kind === "restore") {
    target = Array.from(document.querySelectorAll("#chips .chip-remove")).find(
      (btn) => btn.dataset.key === wanted.key
    );
  } else if (wanted.kind === "remove") {
    // derselbe Platz in der Chip-Zeile (also der nächste Chip), sonst der letzte Chip,
    // sonst der Knopf „wieder hinzufügen“ des eben entfernten Begriffs
    const chips = Array.from(document.querySelectorAll("#chips .chip-remove"));
    target =
      chips[wanted.index] ||
      chips[chips.length - 1] ||
      Array.from(document.querySelectorAll("#chips .chip-restore")).find(
        (btn) => btn.dataset.key === wanted.key
      );
  }
  (target || document.getElementById("q"))?.focus();
}

// --- Facette: von Hand geöffnete Fachgebiete über die Neusuche hinweg offen halten ----------
// Die Facette kommt bei jeder Suche neu (hx-swap-oob); ohne dieses Merken klappte jede Suche
// die Gruppen wieder zu. Gruppen mit gewählter Hauptgruppe öffnet schon der Server.

let openFacetGroups = null;

document.addEventListener("htmx:beforeRequest", function () {
  const groups = document.querySelectorAll("#facet-list details.facet-group");
  if (groups.length) {
    openFacetGroups = new Set(
      Array.from(groups)
        .filter((group) => group.open)
        .map((group) => group.dataset.letter)
    );
  }
});

function reopenFacetGroups() {
  if (!openFacetGroups) {
    return;
  }
  for (const group of document.querySelectorAll("#facet-list details.facet-group")) {
    if (openFacetGroups.has(group.dataset.letter)) {
      group.open = true;
    }
  }
}

document.addEventListener("htmx:afterSettle", function (event) {
  initTabs(document);
  reopenFacetGroups();
  if (event.detail && event.detail.target && event.detail.target.id === "results") {
    restoreFocus();
  }
});

document.addEventListener("DOMContentLoaded", function () {
  initTabs(document);
});

// --- Kopieren ---------------------------------------------------------------------------------

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
