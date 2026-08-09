/* Robonix Package Catalog — submission helper.
 *
 * GitHub Pages is static, so this page cannot open a pull request itself.
 * What it can do is everything up to that point: validate the entry against
 * the same rules the builder enforces, render the exact YAML, and hand the
 * reader to GitHub's edit-and-propose flow, which forks the catalog for them.
 */
(() => {
  "use strict";

  const form = document.querySelector(".submit-form");
  if (!form) return;

  const CATALOG_REPO = "https://github.com/syswonder/robonix-package-catalog";
  const NAME_RE = /^robonix\.[a-z0-9_]+(?:\.[a-z0-9_]+)+$/;
  const REPO_RE = /^https:\/\/github\.com\/([^/\s]+)\/([^/\s#?]+?)(?:\.git)?\/?$/;

  let taken = new Set();
  try {
    taken = new Set(JSON.parse(document.getElementById("catalog-names").textContent));
  } catch (_) {}

  const yamlEl = form.querySelector("[data-yaml]");
  const editLink = form.querySelector("[data-edit-link]");
  const issueLink = form.querySelector("[data-issue-link]");
  const addButton = form.querySelector("[data-add]");
  const copyButton = form.querySelector("[data-copy]");
  const sectionHelp = form.querySelector("[data-section-help]");
  const sectionGroup = form.querySelector("[data-section]").closest(".btn-group");

  let section = "packages";

  const SECTION_HELP = {
    packages: 'Primitives, services and skills all go under <code>packages:</code>.',
    robots: 'Whole-robot deployment repositories go under <code>robots:</code>, and are indexed from <code>robonix_manifest.yaml</code>.',
  };

  /* ------------------------------------------------------------- entries */

  // The first name/repo pair is authored in the markup so the page still shows
  // a usable form without JavaScript; extra pairs are built from the template
  // below and carry the same data attributes.
  const entries = () => Array.from(form.querySelectorAll("[data-entry-row]"));

  const template = () => {
    const block = document.createElement("div");
    block.className = "submit-entry";
    block.dataset.entryRow = "";
    block.innerHTML = `
      <div class="submit-entry-head d-flex align-items-center justify-content-between mb-2">
        <span>Additional entry</span>
        <button type="button" class="submit-remove" data-remove>Remove</button>
      </div>
      <div class="field mb-3">
        <label class="field-label d-block mb-2">Catalog name</label>
        <input class="form-control submit-input" type="text" data-name spellcheck="false"
               autocomplete="off" placeholder="robonix.skill.pick.vertical_grasp">
        <p class="field-error mt-2 mb-0" data-error hidden></p>
      </div>
      <div class="field mb-0">
        <label class="field-label d-block mb-2">GitHub repository</label>
        <input class="form-control submit-input" type="url" data-repo spellcheck="false"
               autocomplete="off" placeholder="https://github.com/owner/repo">
        <p class="field-error mt-2 mb-0" data-error hidden></p>
      </div>`;
    return block;
  };

  /* ---------------------------------------------------------- validation */

  const nameProblem = (name, seen) => {
    if (!name) return "";
    if (!NAME_RE.test(name)) {
      return "Use dotted lowercase segments, starting with robonix. — for example robonix.service.mapping.";
    }
    if (taken.has(name)) return "This name is already in the catalog.";
    if (seen.has(name)) return "You have entered this name twice.";
    return "";
  };

  const repoProblem = (repo) => {
    if (!repo) return "";
    if (!REPO_RE.test(repo)) {
      return "Enter the repository home page URL, like https://github.com/owner/repo.";
    }
    return "";
  };

  const setProblem = (input, message) => {
    const holder = input.closest(".field");
    const error = holder.querySelector("[data-error]");
    if (error) {
      error.textContent = message;
      error.hidden = !message;
    }
    input.setAttribute("aria-invalid", message ? "true" : "false");
  };

  /* ----------------------------------------------------------- rendering */

  // Half-typed input is not yet wrong. A field only starts reporting problems
  // once the reader has left it; after that it keeps reporting until fixed.
  // The YAML preview updates on every keystroke either way.
  const touched = new WeakSet();

  const collect = () => {
    const seen = new Set();
    const rows = [];
    for (const row of entries()) {
      const nameInput = row.querySelector("[data-name]");
      const repoInput = row.querySelector("[data-repo]");
      const name = nameInput.value.trim();
      const repo = repoInput.value.trim().replace(/\.git$/, "").replace(/\/$/, "");

      const nameError = nameProblem(name, seen);
      const repoError = repoProblem(repo);
      setProblem(nameInput, touched.has(nameInput) ? nameError : "");
      setProblem(repoInput, touched.has(repoInput) ? repoError : "");
      if (name) seen.add(name);

      if (name && repo && !nameError && !repoError) rows.push({ name, repo });
    }
    return rows;
  };

  const buildYaml = (rows) => {
    if (!rows.length) {
      return section === "robots"
        ? "robots:\n  - name: robonix.robot.agilex.ranger_mini_v3\n    repo: https://github.com/syswonder/robot-agilex-ranger_mini_v3"
        : "packages:\n  - name: robonix.service.mapping\n    repo: https://github.com/syswonder/service-map-rbnx";
    }
    const body = rows.map((r) => `  - name: ${r.name}\n    repo: ${r.repo}`).join("\n");
    return `${section}:\n${body}`;
  };

  const issueUrl = (rows, yaml) => {
    const what = section === "robots" ? "robot deployment" : "package";
    const title = rows.length === 1 ? `Add ${rows[0].name}` : `Add ${rows.length} catalog entries`;
    const body = [
      `Please add the following to the \`${section}:\` section of \`catalog.yaml\`.`,
      "",
      "```yaml",
      yaml,
      "```",
      "",
      `Each repository is public and has a root-level manifest whose \`name\` matches the ${what} name above.`,
    ].join("\n");
    const params = new URLSearchParams({ title, body, labels: "catalog-submission" });
    return `${CATALOG_REPO}/issues/new?${params.toString()}`;
  };

  const render = () => {
    const rows = collect();
    const yaml = buildYaml(rows);
    yamlEl.textContent = yaml;
    issueLink.href = rows.length ? issueUrl(rows, yaml) : `${CATALOG_REPO}/issues/new`;
    for (const remove of form.querySelectorAll("[data-remove]")) {
      remove.hidden = entries().length < 2;
    }
  };

  /* ------------------------------------------------------------- wiring */

  form.addEventListener("input", render);
  form.addEventListener(
    "blur",
    (event) => {
      const input = event.target.closest("[data-name], [data-repo]");
      if (!input) return;
      touched.add(input);
      render();
    },
    true,
  );

  sectionGroup.addEventListener("click", (event) => {
    const button = event.target.closest("[data-section]");
    if (!button) return;
    section = button.dataset.section;
    for (const other of sectionGroup.querySelectorAll("[data-section]")) {
      const chosen = other === button;
      other.setAttribute("aria-pressed", String(chosen));
      other.classList.toggle("active", chosen);
    }
    sectionHelp.innerHTML = SECTION_HELP[section];
    render();
  });

  addButton.addEventListener("click", () => {
    const block = template();
    addButton.parentElement.insertBefore(block, addButton);
    block.querySelector("[data-name]").focus();
    render();
  });

  form.addEventListener("click", (event) => {
    const remove = event.target.closest("[data-remove]");
    if (!remove) return;
    remove.closest(".submit-entry").remove();
    render();
  });

  copyButton.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(yamlEl.textContent);
      const original = copyButton.textContent;
      copyButton.textContent = "Copied";
      setTimeout(() => {
        copyButton.textContent = original;
      }, 1400);
    } catch (_) {
      // Clipboard access can be denied; the snippet is selectable either way.
      const range = document.createRange();
      range.selectNodeContents(yamlEl);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    }
  });

  editLink.href = `${CATALOG_REPO}/edit/main/catalog.yaml`;
  render();
})();
