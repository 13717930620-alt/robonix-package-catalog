#!/usr/bin/env python3
import argparse
import base64
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import bleach
import markdown
import yaml


GITHUB_RE = re.compile(r"^https://github\.com/([^/]+)/([^/#?]+?)(?:\.git)?/?$")
MAINTAINER_RE = re.compile(r"^[^<>\n]+ <[^<>\s@]+@[^<>\s@]+\.[^<>\s@]+>$")


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def github_json(url: str) -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "robonix-package-catalog",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        fail(f"GitHub request failed {e.code}: {url}\n{body}")
    except Exception as e:
        fail(f"GitHub request failed: {url}: {e}")


def parse_repo(url: str) -> tuple[str, str]:
    m = GITHUB_RE.match(url.strip())
    if not m:
        fail(f"repo must be a GitHub HTTPS URL: {url}")
    return m.group(1), m.group(2)


def load_remote_text(owner: str, repo: str, path: str, branch: str, *, required: bool) -> str:
    try:
        content = github_json(
            f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
        )
    except SystemExit:
        if required:
            raise
        return ""
    if content.get("encoding") != "base64" or "content" not in content:
        if required:
            fail(f"https://github.com/{owner}/{repo}: {path} is not base64 content")
        return ""
    return base64.b64decode(content["content"]).decode("utf-8")


def load_remote_package(repo_url: str) -> tuple[str, dict, str]:
    owner, repo = parse_repo(repo_url)
    meta = github_json(f"https://api.github.com/repos/{owner}/{repo}")
    branch = meta.get("default_branch")
    if not branch:
        fail(f"{repo_url}: missing default_branch")
    raw = load_remote_text(owner, repo, "package_manifest.yaml", branch, required=True)
    try:
        manifest = yaml.safe_load(raw) or {}
    except yaml.YAMLError as e:
        fail(f"{repo_url}: invalid package_manifest.yaml: {e}")
    readme = load_remote_text(owner, repo, "README.md", branch, required=False)
    return branch, manifest, readme


def package_slug(name: str) -> str:
    return name.replace("/", "_")


def read_catalog(path: Path) -> list[dict]:
    raw = yaml.safe_load(path.read_text()) or {}
    packages = raw.get("packages")
    if not isinstance(packages, list):
        fail("catalog.yaml must contain a top-level packages list")
    return packages


def norm_list(value, field: str, package_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        fail(f"{package_name}: {field} must be a list of strings")
    return value


def collect(catalog_path: Path) -> list[dict]:
    entries = read_catalog(catalog_path)
    seen_names = set()
    seen_repos = set()
    out = []
    for entry in entries:
        if not isinstance(entry, dict):
            fail("each catalog package entry must be a mapping")
        name = entry.get("name")
        repo = entry.get("repo")
        if not isinstance(name, str) or not name.strip():
            fail("catalog package entry missing name")
        if not isinstance(repo, str) or not repo.strip():
            fail(f"{name}: catalog package entry missing repo")
        if name in seen_names:
            fail(f"duplicate package name in catalog.yaml: {name}")
        if repo in seen_repos:
            fail(f"duplicate repo in catalog.yaml: {repo}")
        seen_names.add(name)
        seen_repos.add(repo)

        _, repo_name = parse_repo(repo)
        branch, manifest, readme = load_remote_package(repo)
        package = manifest.get("package")
        if not isinstance(package, dict):
            fail(f"{name}: package_manifest.yaml missing package mapping")
        manifest_name = package.get("name")
        version = package.get("version")
        description = package.get("description")
        tags = package.get("tags")
        maintainers = package.get("maintainers")
        if manifest_name != name:
            fail(f"{name}: manifest package.name is {manifest_name!r}, expected {name!r}")
        if not isinstance(version, str) or not version.strip():
            fail(f"{name}: package.version is required")
        if not isinstance(description, str) or not description.strip():
            fail(f"{name}: package.description is required")
        tags = norm_list(tags, "package.tags", name)
        maintainers = norm_list(maintainers, "package.maintainers", name)
        if not maintainers:
            fail(f"{name}: package.maintainers is required")
        for maintainer in maintainers:
            if not MAINTAINER_RE.match(maintainer):
                fail(
                    f"{name}: package.maintainers entries must use 'Name <email@domain>' format: {maintainer!r}"
                )
        capabilities = manifest.get("capabilities") or []
        cap_names = []
        if not isinstance(capabilities, list):
            fail(f"{name}: capabilities must be a list")
        for cap in capabilities:
            if isinstance(cap, dict) and isinstance(cap.get("name"), str):
                cap_names.append(cap["name"])
            else:
                fail(f"{name}: each capability must contain name")
        kind = name.split(".")[1] if name.startswith("robonix.") and "." in name else ""
        out.append(
            {
                "name": name,
                "version": version,
                "description": description,
                "tags": tags,
                "repo": repo,
                "maintainers": maintainers,
                "repo_name": repo_name,
                "default_branch": branch,
                "kind": kind,
                "capabilities": cap_names,
                "readme_url": f"{repo}/blob/{branch}/README.md",
                "_readme_markdown": readme,
            }
        )
    out.sort(key=lambda x: x["name"])
    return out


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def tag_class(tag: str) -> str:
    known = {
        "primitive": "tag-blue",
        "service": "tag-green",
        "skill": "tag-gold",
        "camera": "tag-cyan",
        "lidar": "tag-purple",
        "imu": "tag-slate",
        "chassis": "tag-red",
        "navigation": "tag-green",
        "mapping": "tag-blue",
        "slam": "tag-blue",
        "explore": "tag-gold",
    }
    return known.get(tag, "tag-gray")


def render_tags(tags: list[str]) -> str:
    return " ".join(
        f"<button class=\"tag {tag_class(t)}\" data-tag=\"{html.escape(t)}\">{html.escape(t)}</button>"
        for t in tags
    )


def render_css() -> str:
    return """
    :root {
      color-scheme: light;
      --bg: #ffffff;
      --paper: #ffffff;
      --soft: #f7f7f8;
      --ink: #111827;
      --muted: #667085;
      --line: #e5e7eb;
      --line-strong: #d0d5dd;
      --accent: #2563eb;
      --shadow: 0 1px 2px rgba(16, 24, 40, 0.04);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      background: var(--bg);
      font-family: Arial, Helvetica, sans-serif;
      font-size: 15px;
      letter-spacing: 0;
    }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
    .shell { max-width: 1180px; margin: 0 auto; padding: 0 24px; }
    header {
      color: var(--ink);
      padding: 22px 0 18px;
      border-bottom: 1px solid var(--line);
      margin-bottom: 22px;
    }
    .topline { display: flex; justify-content: space-between; gap: 16px; align-items: center; }
    .brand { font-weight: 700; font-size: 20px; letter-spacing: 0; }
    .api-links { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
    .api-links a {
      color: #344054;
      border: 1px solid var(--line);
      background: var(--soft);
      border-radius: 6px;
      padding: 7px 9px;
      font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
    }
    h1 {
      margin: 0;
      font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 22px;
      line-height: 1.35;
      letter-spacing: 0;
      overflow-wrap: anywhere;
      word-break: break-word;
    }
    .lede { color: var(--muted); max-width: 720px; margin: 8px 0 0; line-height: 1.55; font-size: 15px; }
    main { padding: 0 0 48px; }
    .catalog-frame {
      display: grid;
      grid-template-columns: 265px 1fr;
      gap: 18px;
      align-items: start;
    }
    .panel {
      background: var(--paper);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
      border-radius: 8px;
    }
    .filters { padding: 18px; position: sticky; top: 16px; }
    .filters h2, .content h2 { margin: 0 0 12px; font-size: 16px; }
    .stat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 18px; }
    .stat { border: 1px solid var(--line); border-radius: 6px; padding: 10px; background: var(--soft); }
    .stat strong { display: block; font-size: 22px; }
    .stat span { color: var(--muted); font-size: 12px; }
    .filter-group { border-top: 1px solid var(--line); padding-top: 14px; margin-top: 14px; }
    .filter-buttons { display: flex; gap: 7px; flex-wrap: wrap; }
    .filter-button {
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      border-radius: 999px;
      padding: 6px 10px;
      cursor: pointer;
      font-size: 13px;
    }
    .filter-button.active { background: #1f2933; color: #fff; border-color: #1f2933; }
    .content { padding: 18px; min-width: 0; }
    .toolbar { display: flex; gap: 12px; justify-content: space-between; align-items: center; margin-bottom: 14px; }
    .search {
      width: min(620px, 100%);
      border: 1px solid var(--line-strong);
      background: #fff;
      border-radius: 6px;
      padding: 11px 12px;
      font-size: 15px;
    }
    .count { color: var(--muted); white-space: nowrap; }
    .package-list { display: grid; gap: 10px; }
    .package-card {
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 7px;
      padding: 14px;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 14px;
    }
    .package-title { display: flex; gap: 9px; align-items: baseline; flex-wrap: wrap; margin-bottom: 7px; }
    .package-title a {
      font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 16px;
      font-weight: 700;
      color: #152238;
    }
    .version {
      font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      color: var(--muted);
      font-size: 12px;
      border: 1px solid var(--line);
      border-radius: 4px;
      padding: 2px 5px;
      background: var(--soft);
    }
    .description { color: #343941; margin: 0 0 10px; line-height: 1.45; }
    .meta-line { color: var(--muted); font-size: 13px; display: flex; gap: 14px; flex-wrap: wrap; }
    .meta-line code { font-size: 12px; }
    .card-actions { display: flex; gap: 8px; align-items: start; }
    .card-actions a {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 7px 9px;
      background: var(--soft);
      color: #29303a;
      font-size: 13px;
    }
    .tags { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 10px; }
    .tag {
      border: 0;
      border-radius: 999px;
      padding: 4px 8px;
      font-size: 12px;
      cursor: pointer;
      color: #1c2732;
    }
    .tag-blue { background: #dcecf7; color: #164965; }
    .tag-green { background: #dff0df; color: #23562a; }
    .tag-gold { background: #f3e6bd; color: #6b4b10; }
    .tag-cyan { background: #d8f0f0; color: #165456; }
    .tag-purple { background: #eadff4; color: #4d2d68; }
    .tag-slate { background: #e4e8ee; color: #344052; }
    .tag-red { background: #f5ded8; color: #7b2c1f; }
    .tag-gray { background: #ece8df; color: #4a4b4d; }
    .detail-layout { display: grid; grid-template-columns: 1fr 340px; gap: 18px; align-items: start; }
    .detail-main, .detail-side { padding: 18px; }
    .detail-main h2 { margin-top: 0; font-size: 20px; }
    .kv { display: grid; grid-template-columns: 120px 1fr; gap: 8px 14px; font-size: 14px; }
    .kv div:nth-child(odd) { color: var(--muted); }
    .cap-list { margin: 0; padding-left: 0; list-style: none; display: grid; gap: 7px; }
    .cap-list li {
      font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 13px;
      border: 1px solid var(--line);
      background: var(--soft);
      border-radius: 5px;
      padding: 8px;
      overflow-wrap: anywhere;
    }
    .back { display: inline-block; margin: 18px 0; color: var(--accent); }
    .generated { color: var(--muted); margin-top: 12px; font-size: 13px; }
    .readme {
      line-height: 1.58;
      color: #1f2937;
      overflow-wrap: anywhere;
    }
    .readme h1 { font-size: 26px; }
    .readme h2 { font-size: 21px; border-bottom: 1px solid var(--line); padding-bottom: 7px; margin-top: 28px; }
    .readme h3 { font-size: 17px; margin-top: 22px; }
    .readme pre {
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #f8fafc;
      padding: 12px;
      max-width: 100%;
    }
    .readme code {
      background: #f1f5f9;
      border-radius: 4px;
      padding: 1px 4px;
      font-size: 0.92em;
    }
    .readme pre code { background: transparent; padding: 0; }
    .readme table { border-collapse: collapse; width: 100%; display: block; overflow-x: auto; }
    .readme th, .readme td { border: 1px solid var(--line); padding: 6px 8px; }
    code { font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    @media (max-width: 860px) {
      .catalog-frame, .detail-layout { grid-template-columns: 1fr; }
      .filters { position: static; }
      .toolbar { align-items: stretch; flex-direction: column; }
      .package-card { grid-template-columns: 1fr; }
      .card-actions { justify-content: flex-start; }
    }
    """


def render_site(public_dir: Path, generated_at: str, packages: list[dict]) -> None:
    cards = []
    kinds = sorted({p["kind"] for p in packages if p["kind"]})
    all_tags = sorted({t for p in packages for t in p["tags"]})
    for p in packages:
        detail_href = f"packages/{html.escape(package_slug(p['name']))}/"
        search_text = " ".join(
            [
                p["name"],
                p["version"],
                p["kind"],
                p["description"],
                p["repo"],
                " ".join(p["maintainers"]),
            ]
            + p["tags"]
            + p["capabilities"]
        )
        cards.append(
            f"""<article class="package-card" data-kind="{html.escape(p['kind'])}" data-tags="{html.escape(' '.join(p['tags']))}" data-search="{html.escape(search_text.lower())}">
        <div>
          <div class="package-title">
            <a href="{detail_href}">{html.escape(p['name'])}</a>
            <span class="version">v{html.escape(p['version'])}</span>
          </div>
          <p class="description">{html.escape(p['description'])}</p>
          <div class="meta-line">
            <span>maintainers <strong>{html.escape(', '.join(p['maintainers']))}</strong></span>
            <span>{html.escape(str(len(p['capabilities'])))} capabilities</span>
            <span><code>{html.escape(p['repo_name'])}</code></span>
          </div>
          <div class="tags">{render_tags(p['tags'])}</div>
        </div>
        <div class="card-actions">
          <a href="{detail_href}">Details</a>
          <a href="{html.escape(p['repo'])}">GitHub</a>
        </div>
      </article>"""
        )
    kind_buttons = "".join(
        f"<button class=\"filter-button\" data-kind=\"{html.escape(kind)}\">{html.escape(kind)}</button>"
        for kind in kinds
    )
    tag_buttons = "".join(
        f"<button class=\"filter-button\" data-tag-filter=\"{html.escape(tag)}\">{html.escape(tag)}</button>"
        for tag in all_tags
    )
    public_dir.mkdir(parents=True, exist_ok=True)
    (public_dir / "index.html").write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Robonix Package Catalog</title>
  <style>
{render_css()}
  </style>
</head>
<body>
  <header class="shell">
    <div class="topline">
      <div class="brand">Robonix Package Catalog</div>
      <nav class="api-links">
        <a href="api/packages.json">packages.json</a>
        <a href="api/search.json">search.json</a>
      </nav>
    </div>
  </header>
  <main class="shell">
    <div class="catalog-frame">
      <aside class="panel filters">
        <div class="stat-grid">
          <div class="stat"><strong>{len(packages)}</strong><span>packages</span></div>
          <div class="stat"><strong>{sum(len(p["capabilities"]) for p in packages)}</strong><span>capabilities</span></div>
        </div>
        <div class="filter-group">
          <h2>Kind</h2>
          <div class="filter-buttons" id="kindFilters">
            <button class="filter-button active" data-kind="">All</button>
            {kind_buttons}
          </div>
        </div>
        <div class="filter-group">
          <h2>Tags</h2>
          <div class="filter-buttons" id="tagFilters">
            <button class="filter-button active" data-tag-filter="">All</button>
            {tag_buttons}
          </div>
        </div>
      </aside>
      <section class="panel content">
        <div class="toolbar">
          <input class="search" id="q" placeholder="Search packages, capabilities, tags, maintainers">
          <div class="count" id="count"></div>
        </div>
        <div class="package-list" id="packages">
          {''.join(cards)}
        </div>
      </section>
    </div>
  </main>
  <script>
    const input = document.getElementById('q');
    const cards = Array.from(document.querySelectorAll('.package-card'));
    const count = document.getElementById('count');
    let kind = '';
    let tag = '';
    function setActive(group, selector, value) {{
      for (const button of document.querySelectorAll(group + ' .filter-button')) {{
        button.classList.toggle('active', button.getAttribute(selector) === value);
      }}
    }}
    function applyFilters() {{
      const q = input.value.trim().toLowerCase();
      let shown = 0;
      for (const card of cards) {{
        const matchesText = !q || card.dataset.search.includes(q);
        const matchesKind = !kind || card.dataset.kind === kind;
        const tags = (card.dataset.tags || '').split(' ');
        const matchesTag = !tag || tags.includes(tag);
        const visible = matchesText && matchesKind && matchesTag;
        card.style.display = visible ? '' : 'none';
        if (visible) shown += 1;
      }}
      count.textContent = shown + ' / ' + cards.length + ' packages';
    }}
    input.addEventListener('input', applyFilters);
    document.getElementById('kindFilters').addEventListener('click', (event) => {{
      const button = event.target.closest('[data-kind]');
      if (!button) return;
      kind = button.dataset.kind;
      setActive('#kindFilters', 'data-kind', kind);
      applyFilters();
    }});
    document.getElementById('tagFilters').addEventListener('click', (event) => {{
      const button = event.target.closest('[data-tag-filter]');
      if (!button) return;
      tag = button.dataset.tagFilter;
      setActive('#tagFilters', 'data-tag-filter', tag);
      applyFilters();
    }});
    document.getElementById('packages').addEventListener('click', (event) => {{
      const button = event.target.closest('[data-tag]');
      if (!button) return;
      event.preventDefault();
      tag = button.dataset.tag;
      setActive('#tagFilters', 'data-tag-filter', tag);
      applyFilters();
    }});
    applyFilters();
  </script>
</body>
</html>
""",
        encoding="utf-8",
    )


def render_markdown(md: str) -> str:
    if not md.strip():
        return "<p>No README.md was found in this package repository.</p>"
    rendered = markdown.markdown(md, extensions=["fenced_code", "tables"])
    allowed_tags = set(bleach.sanitizer.ALLOWED_TAGS).union(
        {
            "p",
            "pre",
            "code",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "ul",
            "ol",
            "li",
            "table",
            "thead",
            "tbody",
            "tr",
            "th",
            "td",
            "blockquote",
            "hr",
            "br",
        }
    )
    allowed_attrs = {
        "a": ["href", "title"],
        "code": ["class"],
        "th": ["align"],
        "td": ["align"],
    }
    return bleach.clean(rendered, tags=allowed_tags, attributes=allowed_attrs, strip=True)


def render_package_pages(public_dir: Path, generated_at: str, packages: list[dict]) -> None:
    for p in packages:
        package_dir = public_dir / "packages" / package_slug(p["name"])
        package_dir.mkdir(parents=True, exist_ok=True)
        cap_items = "\n".join(f"<li>{html.escape(cap)}</li>" for cap in p["capabilities"])
        readme_html = render_markdown(p.get("_readme_markdown", ""))
        package_dir.joinpath("index.html").write_text(
            f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(p['name'])} - Robonix Package Catalog</title>
  <style>
{render_css()}
  </style>
</head>
<body>
  <header class="shell">
    <a class="back" href="../../">Back to catalog</a>
    <h1>{html.escape(p['name'])}</h1>
    <p class="lede">{html.escape(p['description'])}</p>
    <div class="generated">Generated on {html.escape(generated_at)}.</div>
  </header>
  <main class="shell">
    <div class="detail-layout">
      <section class="panel detail-main">
        <h2>README</h2>
        <article class="readme">
          {readme_html}
        </article>
      </section>
      <aside class="panel detail-side">
        <h2>Package</h2>
        <div class="kv">
          <div>Version</div><div><code>{html.escape(p['version'])}</code></div>
          <div>Kind</div><div>{html.escape(p['kind'])}</div>
          <div>Maintainers</div><div>{html.escape(', '.join(p['maintainers']))}</div>
          <div>Repository</div><div><a href="{html.escape(p['repo'])}">{html.escape(p['repo_name'])}</a></div>
          <div>Branch</div><div><code>{html.escape(p['default_branch'])}</code></div>
          <div>JSON API</div><div><a href="../../api/packages/{html.escape(p['name'])}.json">package json</a></div>
        </div>
        <div class="tags">{render_tags(p['tags'])}</div>
        <h2>Capabilities</h2>
        <ul class="cap-list">
          {cap_items}
        </ul>
      </aside>
    </div>
  </main>
</body>
</html>
""",
            encoding="utf-8",
        )


def render_readme(path: Path, generated_at: str, packages: list[dict]) -> None:
    lines = [
        "# Robonix Package Catalog",
        "",
        "<!-- This file is generated by scripts/build_catalog.py. Edit catalog.yaml instead. -->",
        "",
        "This repository is the Robonix community package catalog.",
        "",
        "Package source stays in each package's own GitHub repository. The only",
        "manual catalog input is [`catalog.yaml`](catalog.yaml):",
        "",
        "```yaml",
        "packages:",
        "  - name: robonix.service.mapping",
        "    repo: https://github.com/syswonder/service-map-rbnx",
        "```",
        "",
        "To submit a community package, add one `name` + `repo` entry to",
        "`catalog.yaml`. Do not edit generated files by hand.",
        "",
        "## Website",
        "",
        "- Homepage: https://syswonder.github.io/robonix-package-catalog/",
        "- Package list API: https://syswonder.github.io/robonix-package-catalog/api/packages.json",
        "- Search API: https://syswonder.github.io/robonix-package-catalog/api/search.json",
        "- Package detail API: `https://syswonder.github.io/robonix-package-catalog/api/packages/<package-name>.json`",
        "- Package detail page: `https://syswonder.github.io/robonix-package-catalog/packages/<package-name>/`",
        "",
        "Example:",
        "",
        "```js",
        "const res = await fetch('https://syswonder.github.io/robonix-package-catalog/api/packages.json');",
        "const catalog = await res.json();",
        "const mapping = catalog.packages.find(p => p.name === 'robonix.service.mapping');",
        "```",
        "",
        "## Package Manifest",
        "",
        "Each package repository must provide a root-level `package_manifest.yaml`.",
        "The catalog builder reads these fields from that file:",
        "",
        "- `package.name`",
        "- `package.version`",
        "- `package.description`",
        "- `package.tags`",
        "- `package.maintainers`",
        "- `capabilities[].name`",
        "",
        "The `package.name` in `package_manifest.yaml` must exactly match the name in",
        "`catalog.yaml`.",
        "",
        "## Generated Outputs",
        "",
        "CI validates `catalog.yaml`, fetches every package manifest through the GitHub",
        "API, and generates:",
        "",
        "- `generated/api/packages.json`",
        "- `generated/api/search.json`",
        "- `generated/api/packages/<package-name>.json`",
        "- `public/index.html`",
        "- `public/packages/<package-name>/index.html`",
        "- `public/api/...`",
        "",
        "The generated commit uses `[skip ci]`; normal CI only triggers from",
        "`catalog.yaml`, the builder script, the workflow, or manual dispatch.",
        "",
        f"Generated on `{generated_at}`.",
        "",
        "## Packages",
        "",
        "| Name | Version | Kind | Maintainer | Tags | Repository |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for package in packages:
        tags = ", ".join(package["tags"])
        lines.append(
            f"| [`{package['name']}`](https://syswonder.github.io/robonix-package-catalog/packages/{package_slug(package['name'])}/) | `{package['version']}` | `{package['kind']}` | {', '.join(package['maintainers'])} | {tags} | [repo]({package['repo']}) |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="catalog.yaml")
    parser.add_argument("--out", default="generated")
    parser.add_argument("--public", default="public")
    args = parser.parse_args()

    generated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    packages = collect(Path(args.catalog))
    out = Path(args.out)
    public = Path(args.public)
    api = out / "api"
    public_api = public / "api"
    payload = {"generated_at": generated_at, "packages": packages}
    public_packages = [
        {k: v for k, v in package.items() if not k.startswith("_")} for package in packages
    ]
    payload = {"generated_at": generated_at, "packages": public_packages}
    write_json(api / "packages.json", payload)
    write_json(api / "search.json", public_packages)
    write_json(public_api / "packages.json", payload)
    write_json(public_api / "search.json", public_packages)
    for package in public_packages:
        write_json(api / "packages" / f"{package['name']}.json", package)
        write_json(public_api / "packages" / f"{package['name']}.json", package)
    render_site(public, generated_at, packages)
    render_package_pages(public, generated_at, packages)
    render_readme(Path("README.md"), generated_at, packages)
    print(f"generated {len(packages)} package(s)")


if __name__ == "__main__":
    main()
