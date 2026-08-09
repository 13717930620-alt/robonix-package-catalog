/* Robonix Package Catalog — shared behaviour.
 *
 * Three independent pieces, each a no-op when its markup is absent:
 * the theme toggle, the masthead search, and the listing filter/sort.
 */
(() => {
  "use strict";

  /* ------------------------------------------------------------- theme */

  const THEME_KEY = "robonix-catalog-theme";
  const media = window.matchMedia("(prefers-color-scheme: dark)");

  const readStored = () => {
    try {
      const v = localStorage.getItem(THEME_KEY);
      return v === "light" || v === "dark" ? v : null;
    } catch (_) {
      return null;
    }
  };

  // No stored choice means "follow the OS", so the page keeps tracking the
  // media query instead of freezing on whatever it resolved to at load.
  // Bootstrap reads data-bs-theme, which drives its own components too.
  const applyTheme = (resolved) => {
    document.documentElement.dataset.bsTheme = resolved;
  };

  const toggle = document.querySelector("[data-theme-toggle]");
  if (toggle) {
    toggle.addEventListener("click", () => {
      const next = document.documentElement.dataset.bsTheme === "dark" ? "light" : "dark";
      applyTheme(next);
      try {
        localStorage.setItem(THEME_KEY, next);
      } catch (_) {}
    });
  }
  media.addEventListener("change", (event) => {
    if (!readStored()) applyTheme(event.matches ? "dark" : "light");
  });
  window.addEventListener("storage", (event) => {
    if (event.key !== THEME_KEY) return;
    applyTheme(readStored() || (media.matches ? "dark" : "light"));
  });

  /* --------------------------------------------------------- masthead */

  const omni = document.getElementById("omnisearch");
  if (omni) {
    // "/" focuses search from anywhere, the way a code host does it.
    document.addEventListener("keydown", (event) => {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      const tag = (event.target.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select" || event.target.isContentEditable) return;
      event.preventDefault();
      omni.focus();
      omni.select();
    });
    omni.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        omni.value = "";
        omni.dispatchEvent(new Event("input", { bubbles: true }));
        omni.blur();
      }
    });
  }

  /* ---------------------------------------------------------- listing */

  const listing = document.querySelector("[data-listing]");
  if (!listing) {
    // Off the listing pages the masthead field is a jump-to-search box.
    const form = document.getElementById("omnisearch-form");
    if (form) {
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        const q = omni.value.trim();
        window.location.href = form.dataset.target + (q ? "?q=" + encodeURIComponent(q) : "");
      });
    }
    return;
  }

  const noun = listing.dataset.listing;
  const rows = Array.from(listing.querySelectorAll("[data-entry]"));
  const list = listing.querySelector("[data-entry-list]");
  const countEl = listing.querySelector("[data-count]");
  const emptyEl = listing.querySelector("[data-empty]");
  const sortEl = listing.querySelector("[data-sort]");
  const clearEl = listing.querySelector("[data-clear]");
  const params = new URLSearchParams(window.location.search);

  const state = {
    q: params.get("q") || "",
    kind: params.get("kind") || "",
    tag: params.get("tag") || "",
    sort: params.get("sort") || "name",
  };

  if (omni) omni.value = state.q;
  if (sortEl) sortEl.value = state.sort;

  const collator = new Intl.Collator("en");
  const sorters = {
    name: (a, b) => collator.compare(a.dataset.name, b.dataset.name),
    kind: (a, b) =>
      collator.compare(a.dataset.kind, b.dataset.kind) ||
      collator.compare(a.dataset.name, b.dataset.name),
    units: (a, b) =>
      Number(b.dataset.units) - Number(a.dataset.units) ||
      collator.compare(a.dataset.name, b.dataset.name),
  };

  const syncPressed = (selector, attr, value) => {
    for (const button of listing.querySelectorAll(selector)) {
      button.setAttribute("aria-pressed", String(button.dataset[attr] === value));
    }
  };

  const render = () => {
    const q = state.q.trim().toLowerCase();
    let shown = 0;
    for (const row of rows) {
      const visible =
        (!q || row.dataset.search.includes(q)) &&
        (!state.kind || row.dataset.kind === state.kind) &&
        (!state.tag || row.dataset.tags.split(" ").includes(state.tag));
      row.hidden = !visible;
      if (visible) shown += 1;
    }

    const sorter = sorters[state.sort] || sorters.name;
    for (const row of [...rows].sort(sorter)) list.appendChild(row);

    if (countEl) {
      countEl.innerHTML =
        shown === rows.length
          ? `<b>${rows.length}</b> ${noun}`
          : `<b>${shown}</b> of ${rows.length} ${noun}`;
    }
    if (emptyEl) emptyEl.hidden = shown !== 0;
    if (clearEl) clearEl.hidden = !(q || state.kind || state.tag);

    syncPressed("[data-kind-filter]", "kindFilter", state.kind);
    syncPressed("[data-tag-filter]", "tagFilter", state.tag);

    // Skills / Services / Primitives are one page behind three nav links, so
    // the masthead has to follow the kind filter rather than the URL path.
    for (const link of document.querySelectorAll("[data-nav-kind]")) {
      if (link.dataset.navKind === state.kind) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }

    const url = new URL(window.location.href);
    for (const [key, value] of Object.entries(state)) {
      if (value && !(key === "sort" && value === "name")) url.searchParams.set(key, value);
      else url.searchParams.delete(key);
    }
    history.replaceState(null, "", url);
  };

  if (omni) {
    omni.addEventListener("input", () => {
      state.q = omni.value;
      render();
    });
    const form = document.getElementById("omnisearch-form");
    if (form) form.addEventListener("submit", (event) => event.preventDefault());
  }

  listing.addEventListener("click", (event) => {
    const kindButton = event.target.closest("[data-kind-filter]");
    if (kindButton) {
      // Clicking the active facet clears it, so the rail needs no "All" row.
      state.kind = state.kind === kindButton.dataset.kindFilter ? "" : kindButton.dataset.kindFilter;
      render();
      return;
    }
    const tagButton = event.target.closest("[data-tag-filter]");
    if (tagButton) {
      event.preventDefault();
      state.tag = state.tag === tagButton.dataset.tagFilter ? "" : tagButton.dataset.tagFilter;
      render();
      return;
    }
    const more = event.target.closest("[data-facet-more]");
    if (more) {
      for (const hidden of listing.querySelectorAll("[data-tag-overflow]")) hidden.hidden = false;
      more.hidden = true;
    }
  });

  if (sortEl) {
    sortEl.addEventListener("change", () => {
      state.sort = sortEl.value;
      render();
    });
  }

  if (clearEl) {
    clearEl.addEventListener("click", () => {
      state.q = "";
      state.kind = "";
      state.tag = "";
      if (omni) omni.value = "";
      render();
    });
  }

  render();
})();
