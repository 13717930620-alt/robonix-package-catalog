#!/usr/bin/env python3
import argparse
import base64
import datetime as dt
import html
import json
import os
import posixpath
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import bleach
import markdown
import yaml
from pygments.formatters import HtmlFormatter


GITHUB_RE = re.compile(r"^https://github\.com/([^/]+)/([^/#?]+?)(?:\.git)?/?$")
MAINTAINER_RE = re.compile(r"^[^<>\n]+ <[^<>\s@]+@[^<>\s@]+\.[^<>\s@]+>$")
VENDOR_ASSETS = Path("assets/vendor/pico")


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


def github_optional_json(url: str) -> dict | None:
    """Fetch an optional GitHub API resource, returning None only for 404."""
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
        if e.code == 404:
            return None
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
    if required:
        content = github_json(
            f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
        )
    else:
        content = github_optional_json(
            f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
        )
        if content is None:
            return ""
    if content.get("encoding") != "base64" or "content" not in content:
        if required:
            fail(f"https://github.com/{owner}/{repo}: {path} is not base64 content")
        return ""
    return base64.b64decode(content["content"]).decode("utf-8")


def load_remote_manifest(repo_url: str, manifest_path: str) -> tuple[str, dict, str]:
    owner, repo = parse_repo(repo_url)
    meta = github_json(f"https://api.github.com/repos/{owner}/{repo}")
    branch = meta.get("default_branch")
    if not branch:
        fail(f"{repo_url}: missing default_branch")
    raw = load_remote_text(owner, repo, manifest_path, branch, required=True)
    try:
        manifest = yaml.safe_load(raw) or {}
    except yaml.YAMLError as e:
        fail(f"{repo_url}: invalid {manifest_path}: {e}")
    readme = load_remote_text(owner, repo, "README.md", branch, required=False)
    return branch, manifest, readme


def package_slug(name: str) -> str:
    return name.replace("/", "_")


def read_catalog(path: Path) -> list[dict]:
    raw = yaml.safe_load(path.read_text()) or {}
    packages = raw.get("packages")
    if not isinstance(packages, list):
        fail("catalog.yaml must contain a top-level packages list")
    robots = raw.get("robots") or []
    if not isinstance(robots, list):
        fail("catalog.yaml robots must be a list when present")
    entries = []
    for entry in packages:
        if isinstance(entry, dict):
            entry = {**entry, "_catalog_type": "package"}
        entries.append(entry)
    for entry in robots:
        if isinstance(entry, dict):
            entry = {**entry, "_catalog_type": "robot", "manifest": entry.get("manifest", "robonix_manifest.yaml")}
        entries.append(entry)
    return entries


def norm_list(value, field: str, package_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        fail(f"{package_name}: {field} must be a list of strings")
    return value


def validate_catalog_metadata(package_name: str, meta: dict, expected_name: str) -> tuple[str, str, str, list[str], list[str]]:
    if not isinstance(meta, dict):
        fail(f"{package_name}: catalog metadata must be a mapping")
    manifest_name = meta.get("name")
    version = meta.get("version")
    description = meta.get("description")
    license_name = meta.get("license")
    tags = meta.get("tags")
    maintainers = meta.get("maintainers")
    if manifest_name != expected_name:
        fail(f"{package_name}: manifest catalog name is {manifest_name!r}, expected {expected_name!r}")
    if not isinstance(version, str) or not version.strip():
        fail(f"{package_name}: version is required")
    if not isinstance(description, str) or not description.strip():
        fail(f"{package_name}: description is required")
    # Older robot catalog blocks predate the license field. Preserve those
    # repositories while exposing an explicit SPDX-compatible placeholder;
    # all new templates and documentation require a real license value.
    if license_name is None:
        license_name = "NOASSERTION"
        print(
            f"warning: {package_name}: license is missing; using NOASSERTION for backward compatibility",
            file=sys.stderr,
        )
    elif not isinstance(license_name, str) or not license_name.strip():
        fail(f"{package_name}: license must be a non-empty SPDX license string")
    tags = norm_list(tags, "tags", package_name)
    maintainers = norm_list(maintainers, "maintainers", package_name)
    if not maintainers:
        fail(f"{package_name}: maintainers is required")
    for maintainer in maintainers:
        if not MAINTAINER_RE.match(maintainer):
            fail(
                f"{package_name}: maintainers entries must use 'Name <email@domain>' format: {maintainer!r}"
            )
    return version, description, license_name, tags, maintainers


def collect_capabilities(package_name: str, manifest: dict) -> list[str]:
    capabilities = manifest.get("capabilities") or []
    cap_names = []
    if not isinstance(capabilities, list):
        fail(f"{package_name}: capabilities must be a list")
    for cap in capabilities:
        if isinstance(cap, dict) and isinstance(cap.get("name"), str):
            cap_names.append(cap["name"])
        else:
            fail(f"{package_name}: each capability must contain name")
    return cap_names


def collect_deploy_dependencies(package_name: str, manifest: dict) -> list[dict]:
    deps = []
    for section in ("primitive", "service", "skill"):
        entries = manifest.get(section) or []
        if not isinstance(entries, list):
            fail(f"{package_name}: deploy section {section} must be a list")
        for item in entries:
            if not isinstance(item, dict):
                fail(f"{package_name}: deploy section {section} entries must be mappings")
            local_name = item.get("name")
            repo = item.get("url")
            path = item.get("path")
            if not isinstance(local_name, str) or not local_name.strip():
                fail(f"{package_name}: deploy dependency in {section} missing name")
            dep = {
                "section": section,
                "name": local_name,
                "repo": repo if isinstance(repo, str) else "",
                "path": path if isinstance(path, str) else "",
                "branch": item.get("branch") if isinstance(item.get("branch"), str) else "",
                "manifest": item.get("manifest") if isinstance(item.get("manifest"), str) else "",
            }
            deps.append(dep)
    return deps


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
        manifest_path = entry.get("manifest")
        if manifest_path is None:
            manifest_path = "robonix_manifest.yaml" if name.startswith("robonix.robot.") else "package_manifest.yaml"
        if not isinstance(manifest_path, str) or not manifest_path.strip():
            fail(f"{name}: manifest must be a non-empty string")
        branch, manifest, readme = load_remote_manifest(repo, manifest_path)
        catalog_type = entry.get("_catalog_type") or ("robot" if manifest_path == "robonix_manifest.yaml" or name.startswith("robonix.robot.") else "package")
        preview_image_url = ""
        if catalog_type == "robot":
            owner, parsed_repo = parse_repo(repo)
            preview = github_optional_json(
                f"https://api.github.com/repos/{owner}/{parsed_repo}/contents/assets/robot.jpg?ref={urllib.parse.quote(branch, safe='')}"
            )
            if preview and preview.get("type") == "file":
                preview_image_url = preview.get("download_url") or ""
        if catalog_type == "robot":
            meta = manifest.get("catalog")
            version, description, license_name, tags, maintainers = validate_catalog_metadata(name, meta, name)
            cap_names = []
            deploy_dependencies = collect_deploy_dependencies(name, manifest)
        else:
            package = manifest.get("package")
            if not isinstance(package, dict):
                fail(f"{name}: package_manifest.yaml missing package mapping")
            version, description, license_name, tags, maintainers = validate_catalog_metadata(name, package, name)
            cap_names = collect_capabilities(name, manifest)
            deploy_dependencies = []
        kind = name.split(".")[1] if name.startswith("robonix.") and "." in name else ""
        out.append(
            {
                "name": name,
                "version": version,
                "description": description,
                "license": license_name,
                "tags": tags,
                "repo": repo,
                "maintainers": maintainers,
                "repo_name": repo_name,
                "default_branch": branch,
                "kind": kind,
                "catalog_type": catalog_type,
                "manifest": manifest_path,
                "capabilities": cap_names,
                "deploy_dependencies": deploy_dependencies,
                "readme_url": f"{repo}/blob/{branch}/README.md",
                "preview_image_url": preview_image_url,
                "_readme_markdown": readme,
            }
        )
    repo_to_package = {p["repo"]: p["name"] for p in out}
    name_to_slug = {p["name"]: package_slug(p["name"]) for p in out}
    for package in out:
        for dep in package.get("deploy_dependencies", []):
            dep_name = repo_to_package.get(dep.get("repo", ""))
            dep["package_name"] = dep_name or ""
            dep["package_url"] = f"../{name_to_slug[dep_name]}/" if dep_name else ""
    out.sort(key=lambda x: x["name"])
    return out


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def write_api(path: Path, data) -> None:
    """Write an extensionless static JSON API resource for GitHub Pages."""
    write_json(path, data)


def copy_assets(public_dir: Path) -> None:
    asset_dir = public_dir / "assets" / "vendor" / "pico"
    asset_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(VENDOR_ASSETS / "pico.classless.min.css", asset_dir / "pico.classless.min.css")
    shutil.copyfile(VENDOR_ASSETS / "LICENSE.md", asset_dir / "LICENSE.md")


def tag_class(tag: str) -> str:
    known = {
        "primitive": "tag-blue",
        "robot": "tag-red",
        "deploy": "tag-slate",
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
    base_css = """
    :root {
      color-scheme: light;
      --pico-font-family: Arial, Helvetica, sans-serif;
      --pico-border-radius: 6px;
      --pico-spacing: 0.75rem;
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
    .shell {
      width: min(100% - 48px, 1180px);
      max-width: 1180px;
      margin: 0 auto;
    }
    header {
      color: var(--ink);
      border-bottom: 1px solid var(--line);
      margin-bottom: 0;
    }
    header.shell { padding-block: 14px; }
    .topline { display: flex; justify-content: space-between; gap: 16px; align-items: center; }
    .brand { font-weight: 700; font-size: 18px; letter-spacing: 0; }
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
    main.shell { padding-block: 0 34px; }
    .search-strip { padding: 16px 0 14px; }
    .catalog-frame {
      display: grid;
      grid-template-columns: 265px 1fr;
      gap: 14px;
      align-items: start;
    }
    .panel {
      background: var(--paper);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
      border-radius: 8px;
      overflow: hidden;
      margin-bottom: 0;
    }
    .filters { padding: 14px; position: sticky; top: 14px; }
    .filters h2, .content h2 { margin: 0 0 9px; font-size: 15px; }
    .stat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 7px; margin-bottom: 14px; }
    .stat { border: 1px solid var(--line); border-radius: 6px; padding: 8px; background: var(--soft); }
    .stat strong { display: block; font-size: 20px; line-height: 1.15; }
    .stat span { color: var(--muted); font-size: 12px; }
    .filter-group { border-top: 1px solid var(--line); padding-top: 11px; margin-top: 11px; }
    .filter-buttons { display: flex; gap: 6px; flex-wrap: wrap; }
    .filter-button {
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      border-radius: 999px;
      padding: 5px 9px;
      cursor: pointer;
      font-size: 13px;
      margin: 0;
    }
    .filter-button.active { background: #1f2933; color: #fff; border-color: #1f2933; }
    .content { padding: 14px; min-width: 0; }
    .api-reference {
      margin-top: 14px;
      padding: 18px;
      font-size: 14px;
      line-height: 1.45;
    }
    .api-reference h2 { margin: 0 0 8px; font-size: 16px; line-height: 1.3; }
    .api-reference p { margin: 0 0 10px; font-size: 14px; line-height: 1.45; }
    .api-reference table {
      margin: 10px 0 12px;
      width: 100%;
      font-size: 13px;
    }
    .api-reference td,
    .api-reference th {
      vertical-align: top;
      padding: 7px 8px;
    }
    .api-reference pre {
      margin: 0;
      overflow-x: auto;
      border: 1px solid var(--line);
      background: #f8fafc;
      border-radius: 6px;
      padding: 10px;
      font-size: 12px;
      line-height: 1.45;
    }
    .api-reference code { font-size: 0.92em; }
    .muted { color: var(--muted); }
    .toolbar { display: flex; justify-content: flex-end; align-items: center; margin-bottom: 10px; }
    .search {
      width: min(620px, 100%);
      border: 1px solid var(--line-strong);
      background: #fff;
      border-radius: 6px;
      padding: 9px 11px;
      font-size: 15px;
      margin: 0;
    }
    .count { color: var(--muted); white-space: nowrap; }
    .package-list { display: grid; gap: 9px; }
    .package-card {
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 7px;
      padding: 12px;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 12px;
      margin: 0;
    }
    .package-title { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; margin-bottom: 5px; }
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
    .description { color: #343941; margin: 0 0 8px; line-height: 1.38; }
    .meta-line { color: var(--muted); font-size: 13px; display: flex; gap: 14px; flex-wrap: wrap; }
    .meta-line code { font-size: 12px; }
    .card-actions { display: flex; gap: 8px; align-items: start; }
    .card-actions a {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 6px 8px;
      background: var(--soft);
      color: #29303a;
      font-size: 13px;
      margin: 0;
    }
    .card-side {
      display: grid;
      gap: 10px;
      justify-items: end;
      align-content: start;
    }
    .card-preview {
      display: block;
      width: 190px;
      aspect-ratio: 4 / 3;
      object-fit: cover;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--soft);
    }
    .tags { display: flex; gap: 5px; flex-wrap: wrap; margin-top: 8px; }
    .tag {
      border: 0;
      border-radius: 999px;
      padding: 3px 7px;
      font-size: 12px;
      cursor: pointer;
      color: #1c2732;
      margin: 0;
    }
    .tag-blue { background: #dcecf7; color: #164965; }
    .tag-green { background: #dff0df; color: #23562a; }
    .tag-gold { background: #f3e6bd; color: #6b4b10; }
    .tag-cyan { background: #d8f0f0; color: #165456; }
    .tag-purple { background: #eadff4; color: #4d2d68; }
    .tag-slate { background: #e4e8ee; color: #344052; }
    .tag-red { background: #f5ded8; color: #7b2c1f; }
    .tag-gray { background: #ece8df; color: #4a4b4d; }
    .detail-layout {
      display: grid;
      grid-template-columns: 1fr 340px;
      gap: 14px;
      align-items: start;
      margin-top: 16px;
    }
    .detail-main, .detail-side { padding: 14px; }
    .detail-main h2, .detail-side h2 { margin: 0 0 10px; font-size: 18px; }
    .detail-side h2:not(:first-child) { margin-top: 16px; }
    .kv {
      display: grid;
      grid-template-columns: 92px minmax(0, 1fr);
      gap: 4px 10px;
      font-size: 14px;
      line-height: 1.32;
      margin: 0 0 10px;
    }
    .kv div:nth-child(odd) { color: var(--muted); }
    .kv div:nth-child(even) { min-width: 0; overflow-wrap: anywhere; }
    .cap-list { margin: 0; padding-left: 0; list-style: none; display: grid; gap: 5px; }
    .cap-list li {
      font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 13px;
      border: 1px solid var(--line);
      background: var(--soft);
      border-radius: 5px;
      padding: 6px 7px;
      overflow-wrap: anywhere;
      margin: 0;
    }
    .cap-list li span {
      color: var(--muted);
      display: inline-block;
      min-width: 64px;
    }
    .back { display: inline-block; margin: 14px 0; color: var(--accent); }
    .generated { color: var(--muted); margin-top: 12px; font-size: 13px; }
    footer {
      color: var(--muted);
      font-size: 12px;
    }
    footer.shell { padding-block: 18px 28px; }
    .readme {
      line-height: 1.58;
      color: #1f2937;
      overflow-wrap: anywhere;
    }
    .readme h1 { font-size: 24px; margin-bottom: 10px; }
    .readme h2 { font-size: 19px; border-bottom: 1px solid var(--line); padding-bottom: 6px; margin: 22px 0 10px; }
    .readme h3 { font-size: 16px; margin: 18px 0 8px; }
    .readme p, .readme ul, .readme ol { margin-bottom: 10px; }
    .readme pre {
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #f8fafc;
      padding: 10px;
      max-width: 100%;
    }
    .readme code {
      background: #f1f5f9;
      border-radius: 4px;
      padding: 1px 4px;
      font-size: 0.92em;
    }
    .readme pre code { background: transparent; padding: 0; }
    .readme .highlight { margin: 10px 0; }
    .readme .highlight pre { margin: 0; }
    .readme img { display: block; max-width: 100%; height: auto; border-radius: 7px; }
    .readme table { border-collapse: collapse; width: 100%; display: block; overflow-x: auto; }
    .readme th, .readme td { border: 1px solid var(--line); padding: 6px 8px; }
    code { font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    @media (max-width: 860px) {
      .shell { width: min(100% - 28px, 1180px); }
      .catalog-frame, .detail-layout { grid-template-columns: 1fr; }
      .filters { position: static; }
      .toolbar { align-items: stretch; flex-direction: column; }
      .package-card { grid-template-columns: 1fr; }
      .card-side { justify-items: start; }
      .card-actions { justify-content: flex-start; }
      .card-preview { width: min(100%, 360px); }
      .api-reference { padding: 16px; }
    }
    """
    return base_css + "\n" + HtmlFormatter(style="friendly").get_style_defs(".highlight")


def detail_base(package: dict) -> str:
    return "robots" if package.get("catalog_type") == "robot" else "packages"


def render_listing_page(public_dir: Path, generated_at: str, packages: list[dict], *, page: str, title: str, empty_label: str) -> None:
    cards = []
    kinds = sorted({p["kind"] for p in packages if p["kind"]})
    all_tags = sorted({t for p in packages for t in p["tags"]})
    for p in packages:
        detail_href = f"{html.escape(package_slug(p['name']))}/"
        unit_label = "dependencies" if p.get("catalog_type") == "robot" else "capabilities"
        unit_count = len(p.get("deploy_dependencies", [])) if p.get("catalog_type") == "robot" else len(p.get("capabilities", []))
        search_text = " ".join(
            [
                p["name"],
                p["version"],
                p["kind"],
                p["description"],
                p["license"],
                p["repo"],
                " ".join(p["maintainers"]),
            ]
            + p["tags"]
            + p["capabilities"]
            + [d.get("name", "") for d in p.get("deploy_dependencies", [])]
        )
        preview_html = ""
        if p.get("preview_image_url"):
            preview_html = (
                f'<img class="card-preview" src="{html.escape(p["preview_image_url"])}" '
                f'alt="{html.escape(p["name"])} preview" loading="lazy">'
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
            <span>{html.escape(str(unit_count))} {html.escape(unit_label)}</span>
            <span><code>{html.escape(p['repo_name'])}</code></span>
          </div>
          <div class="tags">{render_tags(p['tags'])}</div>
        </div>
        <div class="card-side">
          <div class="card-actions">
            <a href="{detail_href}">Details</a>
            <a href="{html.escape(p['repo'])}">GitHub</a>
          </div>
          {preview_html}
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
    copy_assets(public_dir)
    page_dir = public_dir / page
    page_dir.mkdir(parents=True, exist_ok=True)
    page_dir.joinpath("index.html").write_text(
        f"""<!doctype html>
<html lang="en" data-theme="light">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} - Robonix Package Catalog</title>
  <link rel="stylesheet" href="../assets/vendor/pico/pico.classless.min.css">
  <style>
{render_css()}
  </style>
</head>
<body>
  <header class="shell">
    <div class="topline">
      <div class="brand"><a href="../">Robonix Package Catalog</a> / {html.escape(title)}</div>
      <nav class="api-links">
        <a href="../packages/">Packages</a>
        <a href="../robots/">Robot deployments</a>
        <a href="../api/v1/packages">API</a>
      </nav>
    </div>
  </header>
  <main class="shell">
    <section class="search-strip">
      <input class="search" id="q" placeholder="Search {html.escape(empty_label)}, tags, maintainers">
    </section>
    <div class="catalog-frame">
      <aside class="panel filters">
        <div class="stat-grid">
          <div class="stat"><strong>{len(packages)}</strong><span>{html.escape(empty_label)}</span></div>
          <div class="stat"><strong>{sum(len(p.get("deploy_dependencies", [])) if p.get("catalog_type") == "robot" else len(p.get("capabilities", [])) for p in packages)}</strong><span>indexed items</span></div>
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
          <div class="count" id="count"></div>
        </div>
        <div class="package-list" id="packages">
          {''.join(cards)}
        </div>
      </section>
    </div>
    <section class="panel api-reference">
      <h2>API Reference</h2>
      <p class="muted">Static JSON API hosted by GitHub Pages. Use <code>GET</code>; no API key is required.</p>
      <table>
        <thead><tr><th>Method</th><th>Path</th><th>Parameters</th><th>Response</th></tr></thead>
        <tbody>
          <tr><td><code>GET</code></td><td><code>/api/v1/packages</code></td><td>none</td><td>catalog object with <code>api_version</code>, <code>generated_at</code>, and <code>packages[]</code></td></tr>
          <tr><td><code>GET</code></td><td><code>/api/v1/search</code></td><td>none</td><td>plain package array for client-side filtering</td></tr>
          <tr><td><code>GET</code></td><td><code>/api/v1/package/&lt;package-name&gt;</code></td><td><code>package-name</code>: exact <code>package.name</code>, URL-encoded</td><td>one package object; missing packages return GitHub Pages 404</td></tr>
        </tbody>
      </table>
      <pre><code>const base = 'https://syswonder.github.io/robonix-package-catalog/api/v1';
const catalog = await fetch(`${{base}}/packages`).then(r => r.json());
const detail = await fetch(`${{base}}/package/${{encodeURIComponent('robonix.service.mapping')}}`).then(r => r.json());</code></pre>
    </section>
  </main>
  <footer class="shell">Generated on {html.escape(generated_at)}.</footer>
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
      count.textContent = shown + ' / ' + cards.length + ' {empty_label}';
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


def render_site(public_dir: Path, generated_at: str, packages: list[dict]) -> None:
    package_entries = [p for p in packages if p.get("catalog_type") != "robot"]
    robot_entries = [p for p in packages if p.get("catalog_type") == "robot"]
    public_dir.mkdir(parents=True, exist_ok=True)
    copy_assets(public_dir)
    render_listing_page(public_dir, generated_at, package_entries, page="packages", title="Packages", empty_label="packages")
    render_listing_page(public_dir, generated_at, robot_entries, page="robots", title="Robot deployments", empty_label="robot deployments")
    (public_dir / "index.html").write_text(
        f"""<!doctype html>
<html lang="en" data-theme="light">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Robonix Package Catalog</title>
  <link rel="stylesheet" href="assets/vendor/pico/pico.classless.min.css">
  <style>
{render_css()}
  .entry-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; padding-top: 16px; }}
  .entry-card {{ display: block; padding: 18px; color: var(--ink); }}
  .entry-card:hover {{ text-decoration: none; border-color: var(--line-strong); }}
  .entry-card h2 {{ margin: 0 0 8px; font-size: 18px; }}
  .entry-card strong {{ font-size: 28px; line-height: 1; }}
  @media (max-width: 760px) {{ .entry-grid {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <header class="shell">
    <div class="topline">
      <div class="brand">Robonix Package Catalog</div>
      <nav class="api-links">
        <a href="packages/">Packages</a>
        <a href="robots/">Robot deployments</a>
        <a href="api/v1/packages">API: packages</a>
      </nav>
    </div>
  </header>
  <main class="shell">
    <section class="entry-grid">
      <a class="panel entry-card" href="packages/">
        <h2>Packages</h2>
        <p class="muted">Primitive, service, and skill repositories with capability contracts.</p>
        <strong>{len(package_entries)}</strong>
      </a>
      <a class="panel entry-card" href="robots/">
        <h2>Robot deployments</h2>
        <p class="muted">Whole-robot deploy repositories built around robonix_manifest.yaml.</p>
        <strong>{len(robot_entries)}</strong>
      </a>
    </section>
    <section class="panel api-reference">
      <h2>API Reference</h2>
      <p class="muted">Static JSON API hosted by GitHub Pages. Use <code>GET</code>; no API key is required.</p>
      <table>
        <thead><tr><th>Method</th><th>Path</th><th>Response</th></tr></thead>
        <tbody>
          <tr><td><code>GET</code></td><td><code>/api/v1/packages</code></td><td>ordinary primitive/service/skill package entries</td></tr>
          <tr><td><code>GET</code></td><td><code>/api/v1/robots</code></td><td>robot deployment entries parsed from robonix_manifest.yaml</td></tr>
          <tr><td><code>GET</code></td><td><code>/api/v1/catalog</code></td><td>combined catalog object</td></tr>
          <tr><td><code>GET</code></td><td><code>/api/v1/search</code></td><td>plain combined catalog array for client-side filtering</td></tr>
          <tr><td><code>GET</code></td><td><code>/api/v1/package/&lt;name&gt;</code></td><td>one package or robot deployment object</td></tr>
        </tbody>
      </table>
    </section>
  </main>
  <footer class="shell">Generated on {html.escape(generated_at)}.</footer>
</body>
</html>
""",
        encoding="utf-8",
    )


def absolute_readme_url(value: str, repo_url: str, branch: str, *, raw: bool) -> str:
    """Resolve a README-relative link against the indexed repository root."""
    value = html.unescape(value)
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme or parsed.netloc or value.startswith(("#", "//")):
        return value
    owner, repo = parse_repo(repo_url)
    path = posixpath.normpath(parsed.path.lstrip("/"))
    if path in ("", "."):
        path = "README.md"
    escaped_branch = urllib.parse.quote(branch, safe="")
    escaped_path = urllib.parse.quote(path, safe="/")
    if raw:
        base = f"https://raw.githubusercontent.com/{owner}/{repo}/{escaped_branch}/{escaped_path}"
    else:
        base = f"{repo_url}/blob/{escaped_branch}/{escaped_path}"
    return urllib.parse.urlunsplit(("", "", base, parsed.query, parsed.fragment))


def rewrite_readme_urls(rendered: str, repo_url: str, branch: str) -> str:
    """Make repository-relative README images and links work on catalog pages."""
    def replace(pattern: str, source: str, *, raw: bool) -> str:
        def repl(match: re.Match) -> str:
            resolved = absolute_readme_url(match.group(2), repo_url, branch, raw=raw)
            return match.group(1) + html.escape(resolved, quote=True) + match.group(3)

        return re.sub(pattern, repl, source, flags=re.IGNORECASE)

    rendered = replace(r'(<img\b[^>]*\bsrc=")([^"]+)(")', rendered, raw=True)
    return replace(r'(<a\b[^>]*\bhref=")([^"]+)(")', rendered, raw=False)


def render_markdown(md: str, repo_url: str, branch: str) -> str:
    if not md.strip():
        return "<p>No README.md was found in this package repository.</p>"
    rendered = markdown.markdown(
        md,
        extensions=["tables", "pymdownx.highlight", "pymdownx.superfences"],
        extension_configs={
            "pymdownx.highlight": {
                "css_class": "highlight",
                "guess_lang": False,
                "use_pygments": True,
            }
        },
    )
    rendered = rewrite_readme_urls(rendered, repo_url, branch)
    allowed_tags = set(bleach.sanitizer.ALLOWED_TAGS).union(
        {
            "div",
            "span",
            "p",
            "pre",
            "code",
            "img",
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
        "div": ["class"],
        "span": ["class"],
        "img": ["src", "alt", "title", "width", "height", "loading"],
        "th": ["align"],
        "td": ["align"],
    }
    return bleach.clean(rendered, tags=allowed_tags, attributes=allowed_attrs, strip=True)


def render_package_pages(public_dir: Path, generated_at: str, packages: list[dict]) -> None:
    for p in packages:
        base = detail_base(p)
        package_dir = public_dir / base / package_slug(p["name"])
        package_dir.mkdir(parents=True, exist_ok=True)
        if p.get("catalog_type") == "robot":
            cap_title = "Deployment Packages"
            dep_items = []
            for dep in p.get("deploy_dependencies", []):
                dep_name = dep.get("package_name") or dep.get("repo") or dep.get("path") or dep.get("name")
                if dep.get("package_name"):
                    dep_label = f"<a href=\"../../packages/{html.escape(package_slug(dep['package_name']))}/\">{html.escape(dep['package_name'])}</a>"
                elif dep.get("repo"):
                    dep_label = f"<a href=\"{html.escape(dep['repo'])}\">{html.escape(dep['repo'])}</a>"
                else:
                    dep_label = html.escape(dep_name)
                dep_items.append(
                    f"<li><span>{html.escape(dep.get('section', ''))}</span> <code>{html.escape(dep.get('name', ''))}</code><br>{dep_label}</li>"
                )
            cap_items = "\n".join(dep_items)
        else:
            cap_title = "Capabilities"
            cap_items = "\n".join(f"<li>{html.escape(cap)}</li>" for cap in p["capabilities"])
        readme_html = render_markdown(
            p.get("_readme_markdown", ""), p["repo"], p["default_branch"]
        )
        package_dir.joinpath("index.html").write_text(
            f"""<!doctype html>
<html lang="en" data-theme="light">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(p['name'])} - Robonix Package Catalog</title>
  <link rel="stylesheet" href="../../assets/vendor/pico/pico.classless.min.css">
  <style>
{render_css()}
  </style>
</head>
<body>
  <header class="shell">
    <a class="back" href="../">Back to {html.escape(base)}</a>
    <h1>{html.escape(p['name'])}</h1>
    <p class="lede">{html.escape(p['description'])}</p>
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
          <div>License</div><div><code>{html.escape(p['license'])}</code></div>
          <div>Kind</div><div>{html.escape(p['kind'])}</div>
          <div>Type</div><div>{html.escape(p['catalog_type'])}</div>
          <div>Maintainers</div><div>{html.escape(', '.join(p['maintainers']))}</div>
          <div>Repository</div><div><a href="{html.escape(p['repo'])}">{html.escape(p['repo_name'])}</a></div>
          <div>Branch</div><div><code>{html.escape(p['default_branch'])}</code></div>
          <div>Manifest</div><div><code>{html.escape(p['manifest'])}</code></div>
          <div>API</div><div><a href="../../api/v1/package/{html.escape(p['name'])}">package metadata</a></div>
        </div>
        <div class="tags">{render_tags(p['tags'])}</div>
        <h2>{html.escape(cap_title)}</h2>
        <ul class="cap-list">
          {cap_items}
        </ul>
      </aside>
    </div>
  </main>
  <footer class="shell">Generated on {html.escape(generated_at)}.</footer>
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
        "Package source and robot deployment manifests stay in their own GitHub",
        "repositories. The only manual catalog input is the root-level",
        "[`catalog.yaml`](catalog.yaml). Ordinary packages go under `packages:`;",
        "whole-robot deploy repositories go under `robots:`:",
        "",
        "```yaml",
        "packages:",
        "  - name: robonix.service.mapping",
        "    repo: https://github.com/syswonder/service-map-rbnx",
        "",
        "robots:",
        "  - name: robonix.robot.agilex.ranger_mini_v3",
        "    repo: https://github.com/syswonder/robot-agilex-ranger_mini_v3",
        "```",
        "",
        "To submit a community package or robot deployment, add one `name` + `repo`",
        "entry to the correct section in `catalog.yaml`. Do not edit generated files by hand.",
        "",
        "## Website",
        "",
        "- Homepage: https://syswonder.github.io/robonix-package-catalog/",
        "- Package page: https://syswonder.github.io/robonix-package-catalog/packages/",
        "- Robot deployment page: https://syswonder.github.io/robonix-package-catalog/robots/",
        "- Full catalog API: `GET https://syswonder.github.io/robonix-package-catalog/api/v1/catalog`",
        "- Package list API: `GET https://syswonder.github.io/robonix-package-catalog/api/v1/packages`",
        "- Robot deployment API: `GET https://syswonder.github.io/robonix-package-catalog/api/v1/robots`",
        "- Search index API: `GET https://syswonder.github.io/robonix-package-catalog/api/v1/search`",
        "- Package detail API: `GET https://syswonder.github.io/robonix-package-catalog/api/v1/package/<package-name>`",
        "- Package detail page: `https://syswonder.github.io/robonix-package-catalog/packages/<package-name>/`",
        "- Robot detail page: `https://syswonder.github.io/robonix-package-catalog/robots/<robot-name>/`",
        "",
        "The catalog is hosted on GitHub Pages, so these are static JSON resources",
        "with stable API-style paths. Clients should treat the shape below as the v1",
        "contract.",
        "",
        "## API Reference",
        "",
        "All endpoints are static JSON resources served from GitHub Pages. Use",
        "`GET`; no API key is required. There are no server-side query parameters",
        "because Pages is static. Filter by name, kind, tag, maintainer, or",
        "capability on the client using the returned JSON.",
        "",
        "| Method | Path | Parameters | Response |",
        "| --- | --- | --- | --- |",
        "| `GET` | `/api/v1/catalog` | none | combined catalog object with both ordinary packages and robot deployments |",
        "| `GET` | `/api/v1/packages` | none | ordinary primitive/service/skill packages only |",
        "| `GET` | `/api/v1/robots` | none | robot deployment entries only |",
        "| `GET` | `/api/v1/search` | none | plain combined catalog array, intended for client-side search/filter indexes |",
        "| `GET` | `/api/v1/package/<package-name>` | `package-name`: exact catalog `name`, URL-encoded | one ordinary package or robot deployment object; missing entries return GitHub Pages `404` |",
        "",
        "Package object fields:",
        "",
        "| Field | Type | Meaning |",
        "| --- | --- | --- |",
        "| `name` | string | canonical package name, e.g. `robonix.service.mapping` |",
        "| `version` | string | package version from `package_manifest.yaml` |",
        "| `description` | string | short package description |",
        "| `license` | string | SPDX license identifier; legacy entries without one are exposed as `NOASSERTION` |",
        "| `tags` | string[] | UI/search tags |",
        "| `maintainers` | string[] | maintainers in `Name <email@domain>` format |",
        "| `repo` | string | GitHub repository URL |",
        "| `repo_name` | string | repository name without owner |",
        "| `default_branch` | string | package repository default branch used for indexing |",
        "| `kind` | string | `primitive`, `service`, `skill`, or `robot` inferred from catalog name |",
        "| `catalog_type` | string | `package` for ordinary packages, `robot` for whole-robot deployments |",
        "| `manifest` | string | source manifest path, usually `package_manifest.yaml` or `robonix_manifest.yaml` |",
        "| `capabilities` | string[] | declared Robonix contract IDs |",
        "| `deploy_dependencies` | object[] | robot deployment dependencies parsed from `robonix_manifest.yaml` |",
        "| `readme_url` | string | GitHub README URL for the indexed branch |",
        "| `preview_image_url` | string | optional robot preview discovered at `assets/robot.jpg`; empty when absent |",
        "",
        "### JavaScript",
        "",
        "```js",
        "const base = 'https://syswonder.github.io/robonix-package-catalog/api/v1';",
        "const res = await fetch(`${base}/packages`);",
        "const catalog = await res.json();",
        "const mapping = catalog.packages.find(p => p.name === 'robonix.service.mapping');",
        "",
        "const detail = await fetch(`${base}/package/${encodeURIComponent(mapping.name)}`)",
        "  .then(r => r.json());",
        "```",
        "",
        "### curl",
        "",
        "```bash",
        "curl -s https://syswonder.github.io/robonix-package-catalog/api/v1/packages",
        "curl -s https://syswonder.github.io/robonix-package-catalog/api/v1/package/robonix.service.mapping",
        "```",
        "",
        "### Python",
        "",
        "```python",
        "import urllib.request, json",
        "",
        "base = 'https://syswonder.github.io/robonix-package-catalog/api/v1'",
        "catalog = json.load(urllib.request.urlopen(f'{base}/packages'))",
        "mapping = next(p for p in catalog['packages'] if p['name'] == 'robonix.service.mapping')",
        "detail = json.load(urllib.request.urlopen(f\"{base}/package/{mapping['name']}\"))",
        "```",
        "",
        "### API schema",
        "",
        "`GET /api/v1/packages` returns:",
        "",
        "```json",
        "{",
        "  \"api_version\": \"1\",",
        "  \"generated_at\": \"2026-07-06T12:00:00+00:00\",",
        "  \"packages\": [",
        "    {",
        "      \"name\": \"robonix.service.mapping\",",
        "      \"version\": \"0.4.0\",",
        "      \"description\": \"Map and SLAM service package for Robonix.\",",
        "      \"license\": \"MulanPSL-2.0\",",
        "      \"tags\": [\"service\", \"mapping\", \"slam\"],",
        "      \"maintainers\": [\"wheatfox <wheatfox17@icloud.com>\"],",
        "      \"repo\": \"https://github.com/syswonder/service-map-rbnx\",",
        "      \"repo_name\": \"service-map-rbnx\",",
        "      \"default_branch\": \"main\",",
        "      \"kind\": \"service\",",
        "      \"capabilities\": [\"robonix/service/map/save_map\"],",
        "      \"readme_url\": \"https://github.com/syswonder/service-map-rbnx/blob/main/README.md\"",
        "    }",
        "  ]",
        "}",
        "```",
        "",
        "`GET /api/v1/robots` returns robot deployments under a top-level `robots[]` field.",
        "",
        "`GET /api/v1/search` returns the combined catalog entries as a plain array.",
        "",
        "`GET /api/v1/package/<package-name>` returns one package object.",
        "",
        "## Package Manifest",
        "",
        "Each package repository must provide a root-level `package_manifest.yaml`.",
        "The catalog builder reads these fields from that file:",
        "",
        "- `package.name`",
        "- `package.version`",
        "- `package.description`",
        "- `package.license`",
        "- `package.tags`",
        "- `package.maintainers`",
        "- `capabilities[].name`",
        "",
        "The `package.name` in `package_manifest.yaml` must exactly match the name in",
        "`catalog.yaml`.",
        "",
        "## Robot Deployment Manifest",
        "",
        "Robot deployment repositories are indexed from root-level `robonix_manifest.yaml`.",
        "They do not need a separate `package_manifest.yaml`. The catalog metadata lives",
        "under a top-level `catalog:` block with the same fields as package metadata:",
        "",
        "```yaml",
        "manifestVersion: 1",
        "name: robonix-ranger-mini-deploy",
        "catalog:",
        "  name: robonix.robot.agilex.ranger_mini_v3",
        "  version: 0.1.0",
        "  description: Robonix deploy manifest for the AgileX Ranger Mini v3 robot.",
        "  license: Apache-2.0",
        "  tags: [robot, deploy, agilex, ranger_mini_v3]",
        "  maintainers:",
        "    - wheatfox <wheatfox17@icloud.com>",
        "```",
        "",
        "A robot deployment repository may add `assets/robot.jpg`. When present,",
        "the catalog exposes its raw URL as `preview_image_url` and displays the",
        "photo in the robot deployment list. Repositories without the file keep",
        "the same metadata and layout without an image placeholder.",
        "",
        "The builder also parses `primitive:`, `service:`, and `skill:` entries from",
        "`robonix_manifest.yaml` into `deploy_dependencies[]`, linking dependencies",
        "back to cataloged ordinary packages when their repository is known.",
        "",
        "## Generated Outputs",
        "",
        "CI validates `catalog.yaml`, fetches every package manifest through the GitHub",
        "API, and generates:",
        "",
        "- `generated/api/v1/packages`",
        "- `generated/api/v1/robots`",
        "- `generated/api/v1/catalog`",
        "- `generated/api/v1/search`",
        "- `generated/api/v1/package/<package-name>`",
        "- `public/index.html`",
        "- `public/packages/index.html`",
        "- `public/packages/<package-name>/index.html`",
        "- `public/robots/index.html`",
        "- `public/robots/<robot-name>/index.html`",
        "- `public/api/...`",
        "",
        "For compatibility, CI also writes `.json` aliases under `api/`, but new",
        "integrations should use the `/api/v1/...` paths above.",
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
        page_base = "robots" if package.get("catalog_type") == "robot" else "packages"
        lines.append(
            f"| [`{package['name']}`](https://syswonder.github.io/robonix-package-catalog/{page_base}/{package_slug(package['name'])}/) | `{package['version']}` | `{package['kind']}` | {', '.join(package['maintainers'])} | {tags} | [repo]({package['repo']}) |"
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
    if out.exists():
        shutil.rmtree(out)
    if public.exists():
        shutil.rmtree(public)
    api = out / "api"
    public_api = public / "api"
    public_packages = [
        {k: v for k, v in package.items() if not k.startswith("_")} for package in packages
    ]
    package_entries = [p for p in public_packages if p.get("catalog_type") != "robot"]
    robot_entries = [p for p in public_packages if p.get("catalog_type") == "robot"]
    catalog_payload = {"api_version": "1", "generated_at": generated_at, "packages": public_packages}
    package_payload = {"api_version": "1", "generated_at": generated_at, "packages": package_entries}
    robot_payload = {"api_version": "1", "generated_at": generated_at, "robots": robot_entries}
    write_json(api / "catalog.json", catalog_payload)
    write_json(api / "packages.json", package_payload)
    write_json(api / "robots.json", robot_payload)
    write_json(api / "search.json", public_packages)
    write_json(public_api / "catalog.json", catalog_payload)
    write_json(public_api / "packages.json", package_payload)
    write_json(public_api / "robots.json", robot_payload)
    write_json(public_api / "search.json", public_packages)
    write_api(api / "v1" / "catalog", catalog_payload)
    write_api(api / "v1" / "packages", package_payload)
    write_api(api / "v1" / "robots", robot_payload)
    write_api(api / "v1" / "search", public_packages)
    write_api(public_api / "v1" / "catalog", catalog_payload)
    write_api(public_api / "v1" / "packages", package_payload)
    write_api(public_api / "v1" / "robots", robot_payload)
    write_api(public_api / "v1" / "search", public_packages)
    for package in public_packages:
        write_json(api / "packages" / f"{package['name']}.json", package)
        write_json(public_api / "packages" / f"{package['name']}.json", package)
        write_api(api / "v1" / "package" / package["name"], package)
        write_api(public_api / "v1" / "package" / package["name"], package)
    render_site(public, generated_at, packages)
    render_package_pages(public, generated_at, packages)
    render_readme(Path("README.md"), generated_at, packages)
    print(f"generated {len(packages)} package(s)")


if __name__ == "__main__":
    main()
