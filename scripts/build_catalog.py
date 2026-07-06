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

import yaml


GITHUB_RE = re.compile(r"^https://github\.com/([^/]+)/([^/#?]+?)(?:\.git)?/?$")


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


def load_remote_manifest(repo_url: str) -> tuple[str, dict]:
    owner, repo = parse_repo(repo_url)
    meta = github_json(f"https://api.github.com/repos/{owner}/{repo}")
    branch = meta.get("default_branch")
    if not branch:
        fail(f"{repo_url}: missing default_branch")
    content = github_json(
        f"https://api.github.com/repos/{owner}/{repo}/contents/package_manifest.yaml?ref={branch}"
    )
    if content.get("encoding") != "base64" or "content" not in content:
        fail(f"{repo_url}: package_manifest.yaml is not base64 content")
    raw = base64.b64decode(content["content"]).decode("utf-8")
    try:
        manifest = yaml.safe_load(raw) or {}
    except yaml.YAMLError as e:
        fail(f"{repo_url}: invalid package_manifest.yaml: {e}")
    return branch, manifest


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

        branch, manifest = load_remote_manifest(repo)
        package = manifest.get("package")
        if not isinstance(package, dict):
            fail(f"{name}: package_manifest.yaml missing package mapping")
        manifest_name = package.get("name")
        version = package.get("version")
        description = package.get("description")
        tags = package.get("tags")
        if manifest_name != name:
            fail(f"{name}: manifest package.name is {manifest_name!r}, expected {name!r}")
        if not isinstance(version, str) or not version.strip():
            fail(f"{name}: package.version is required")
        if not isinstance(description, str) or not description.strip():
            fail(f"{name}: package.description is required")
        tags = norm_list(tags, "package.tags", name)
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
                "default_branch": branch,
                "kind": kind,
                "capabilities": cap_names,
            }
        )
    out.sort(key=lambda x: x["name"])
    return out


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def render_site(public_dir: Path, generated_at: str, packages: list[dict]) -> None:
    rows = []
    for p in packages:
        tags = " ".join(f"<span>{html.escape(t)}</span>" for t in p["tags"])
        caps = html.escape(str(len(p["capabilities"])))
        rows.append(
            "<tr>"
            f"<td><a href=\"api/packages/{html.escape(p['name'])}.json\">{html.escape(p['name'])}</a></td>"
            f"<td>{html.escape(p['version'])}</td>"
            f"<td>{html.escape(p['kind'])}</td>"
            f"<td>{html.escape(p['description'])}</td>"
            f"<td class=\"tags\">{tags}</td>"
            f"<td>{caps}</td>"
            f"<td><a href=\"{html.escape(p['repo'])}\">repo</a></td>"
            "</tr>"
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
    :root {{ color-scheme: light; }}
    body {{ font-family: Arial, sans-serif; margin: 0; color: #1f2933; background: #f6f7f9; }}
    header {{ background: #20252d; color: white; padding: 28px 32px; border-bottom: 4px solid #d2a64a; }}
    header h1 {{ font-size: 30px; margin: 0 0 6px; }}
    header p {{ margin: 0; color: #cbd5e1; }}
    main {{ margin: 24px auto 40px; max-width: 1180px; padding: 0 20px; }}
    .panel {{ background: white; border: 1px solid #d0d7de; border-radius: 6px; padding: 16px; }}
    .meta {{ color: #667085; margin-bottom: 12px; }}
    .api {{ display: flex; gap: 8px; flex-wrap: wrap; margin: 12px 0 16px; }}
    .api a {{ background: #eef2f6; color: #1f2933; text-decoration: none; padding: 5px 8px; border-radius: 4px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }}
    input {{ width: min(720px, 100%); padding: 9px 11px; font-size: 14px; border: 1px solid #b8c0cc; border-radius: 4px; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 14px; font-size: 14px; background: white; }}
    th, td {{ border-bottom: 1px solid #d0d7de; padding: 9px; text-align: left; vertical-align: top; }}
    th {{ background: #f0f2f5; font-weight: 700; }}
    tr:hover {{ background: #fbfcfe; }}
    .tags span {{ display: inline-block; margin: 0 4px 4px 0; padding: 1px 6px; background: #eef2f6; border-radius: 3px; font-size: 12px; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
  </style>
</head>
<body>
  <header>
    <h1>Robonix Package Catalog</h1>
    <p>Search Robonix packages by name, tag, capability, and repository.</p>
  </header>
  <main>
    <section class="panel">
      <div class="meta">Generated on {html.escape(generated_at)}.</div>
      <div class="api">
        <a href="api/packages.json">api/packages.json</a>
        <a href="api/search.json">api/search.json</a>
      </div>
      <input id="q" placeholder="Search by name, tag, description, capability, or repo">
      <table id="packages">
        <thead>
          <tr><th>Name</th><th>Version</th><th>Kind</th><th>Description</th><th>Tags</th><th>Caps</th><th>Repo</th></tr>
        </thead>
        <tbody>
          {''.join(rows)}
        </tbody>
      </table>
    </section>
  </main>
  <script>
    const input = document.getElementById('q');
    const rows = Array.from(document.querySelectorAll('#packages tbody tr'));
    input.addEventListener('input', () => {{
      const q = input.value.toLowerCase();
      for (const row of rows) {{
        row.style.display = row.textContent.toLowerCase().includes(q) ? '' : 'none';
      }}
    }});
  </script>
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
        "- `public/api/...`",
        "",
        "The generated commit uses `[skip ci]`; normal CI only triggers from",
        "`catalog.yaml`, the builder script, the workflow, or manual dispatch.",
        "",
        f"Generated on `{generated_at}`.",
        "",
        "## Packages",
        "",
        "| Name | Version | Kind | Tags | Repository |",
        "| --- | --- | --- | --- | --- |",
    ]
    for package in packages:
        tags = ", ".join(package["tags"])
        lines.append(
            f"| `{package['name']}` | `{package['version']}` | `{package['kind']}` | {tags} | [repo]({package['repo']}) |"
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
    write_json(api / "packages.json", payload)
    write_json(api / "search.json", packages)
    write_json(public_api / "packages.json", payload)
    write_json(public_api / "search.json", packages)
    for package in packages:
        write_json(api / "packages" / f"{package['name']}.json", package)
        write_json(public_api / "packages" / f"{package['name']}.json", package)
    render_site(public, generated_at, packages)
    render_readme(Path("README.md"), generated_at, packages)
    print(f"generated {len(packages)} package(s)")


if __name__ == "__main__":
    main()
