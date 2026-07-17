import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_catalog", ROOT / "scripts" / "build_catalog.py"
)
BUILD_CATALOG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD_CATALOG)


class MarkdownRenderingTests(unittest.TestCase):
    def test_yaml_fence_is_highlighted_and_relative_image_is_preserved(self):
        rendered = BUILD_CATALOG.render_markdown(
            "1. Configure the deployment:\n\n"
            "   ```yaml\n"
            "   manifestVersion: 1\n"
            "   ```\n\n"
            "![Robot](assets/robot.jpg)",
            "https://github.com/syswonder/example-robot",
            "main",
        )

        self.assertIn('class="highlight"', rendered)
        self.assertIn('class="nt"', rendered)
        self.assertIn("raw.githubusercontent.com/syswonder/example-robot/main/assets/robot.jpg", rendered)
        self.assertIn("<img", rendered)

    def test_optional_readme_is_empty_only_for_a_real_404(self):
        with mock.patch.object(BUILD_CATALOG, "github_optional_json", return_value=None):
            value = BUILD_CATALOG.load_remote_text(
                "syswonder", "missing", "README.md", "main", required=False
            )

        self.assertEqual(value, "")

    def test_optional_readme_does_not_swallow_transport_failure(self):
        with mock.patch.object(
            BUILD_CATALOG,
            "github_optional_json",
            side_effect=RuntimeError("transport failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "transport failed"):
                BUILD_CATALOG.load_remote_text(
                    "syswonder", "example", "README.md", "main", required=False
                )


class RobotListingTests(unittest.TestCase):
    def test_robot_preview_is_rendered_below_actions(self):
        package = {
            "name": "robonix.robot.example",
            "version": "0.1.0",
            "kind": "robot",
            "description": "Example robot",
            "license": "Apache-2.0",
            "repo": "https://github.com/syswonder/example-robot",
            "repo_name": "example-robot",
            "default_branch": "main",
            "catalog_type": "robot",
            "tags": ["robot"],
            "maintainers": ["Example <example@example.com>"],
            "capabilities": [],
            "deploy_dependencies": [],
            "preview_image_url": "https://raw.githubusercontent.com/syswonder/example-robot/main/assets/robot.jpg",
        }

        with tempfile.TemporaryDirectory() as tmp:
            public = Path(tmp)
            BUILD_CATALOG.render_listing_page(
                public,
                "2026-07-14T00:00:00+00:00",
                [package],
                page="robots",
                title="Robot deployments",
                empty_label="robot deployments",
            )
            rendered = (public / "robots" / "index.html").read_text()

        self.assertIn('class="card-side"', rendered)
        self.assertIn('class="card-preview"', rendered)
        self.assertIn('width="380" height="285"', rendered)
        self.assertIn('decoding="async" fetchpriority="low"', rendered)
        self.assertLess(rendered.index('class="card-actions"'), rendered.index('class="card-preview"'))

    def test_robot_preview_is_resized_to_responsive_webp_assets(self):
        image_buffer = io.BytesIO()
        Image.new("RGB", (1600, 900), (39, 54, 137)).save(image_buffer, "JPEG", quality=95)
        package = {
            "name": "robonix.robot.example",
            "preview_image_url": "https://raw.githubusercontent.com/syswonder/example/main/assets/robot.jpg",
        }

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            BUILD_CATALOG, "download_bytes", return_value=image_buffer.getvalue()
        ):
            public = Path(tmp)
            BUILD_CATALOG.prepare_preview_images(public, [package])
            small = public / package["_preview_image_380"]
            large = public / package["_preview_image_720"]

            self.assertTrue(small.is_file())
            self.assertTrue(large.is_file())
            with Image.open(small) as preview:
                self.assertEqual(preview.format, "WEBP")
                self.assertEqual(preview.size, (380, 285))
            with Image.open(large) as preview:
                self.assertEqual(preview.size, (720, 540))

    def test_listing_has_mobile_navigation_filters_and_api_rows(self):
        package = {
            "name": "robonix.robot.example",
            "version": "0.1.0",
            "kind": "robot",
            "description": "Example robot",
            "license": "Apache-2.0",
            "repo": "https://github.com/syswonder/example-robot",
            "repo_name": "example-robot",
            "default_branch": "main",
            "catalog_type": "robot",
            "tags": ["robot"],
            "maintainers": ["Example <example@example.com>"],
            "capabilities": [],
            "deploy_dependencies": [],
        }

        with tempfile.TemporaryDirectory() as tmp:
            public = Path(tmp)
            BUILD_CATALOG.render_listing_page(
                public,
                "2026-07-14T00:00:00+00:00",
                [package],
                page="robots",
                title="Robot deployments",
                empty_label="robot deployments",
            )
            rendered = (public / "robots" / "index.html").read_text()

        self.assertIn('<details class="panel filters" open>', rendered)
        self.assertIn("<summary>Filters and catalog summary</summary>", rendered)
        self.assertIn("filters.open = false", rendered)
        self.assertIn("top: calc(var(--site-header-height) + var(--sticky-gap))", rendered)
        self.assertIn("max-height: calc(100dvh - var(--site-header-height) - 28px)", rendered)
        self.assertIn("new ResizeObserver(updateHeaderHeight).observe(header)", rendered)
        self.assertIn("max-height: none", rendered)
        self.assertIn("@media (max-width: 520px)", rendered)
        self.assertIn(".topline { align-items: stretch; flex-direction: column; }", rendered)
        self.assertIn("@media (prefers-reduced-motion: reduce)", rendered)
        self.assertNotIn('class="brand-bus"', rendered)
        self.assertIn('class="clear-filters"', rendered)
        self.assertIn('family=Noto+Sans+SC', rendered)
        self.assertIn('--font-sans: "Noto Sans CJK SC", "Noto Sans SC"', rendered)
        self.assertIn('--font-mono: "JetBrains Mono", "Noto Sans CJK SC"', rendered)
        self.assertIn('data-label="Method"', rendered)
        self.assertIn('data-label="Response"', rendered)
        self.assertIn('href="../api/view/?resource=packages"', rendered)
        self.assertNotIn('<span><code>example-robot</code></span>', rendered)
        self.assertIn(
            '<link rel="icon" type="image/svg+xml" href="../assets/robonix-mark.svg">',
            rendered,
        )
        self.assertIn(
            'class="brand-mark" src="../assets/robonix-mark.svg" alt="" aria-hidden="true"',
            rendered,
        )

    def test_homepage_is_search_first_and_links_package_layers(self):
        package = {
            "name": "robonix.service.example",
            "version": "0.1.0",
            "kind": "service",
            "description": "Example service",
            "license": "Apache-2.0",
            "repo": "https://github.com/syswonder/example-service",
            "repo_name": "example-service",
            "default_branch": "main",
            "catalog_type": "package",
            "manifest": "package_manifest.yaml",
            "tags": ["service"],
            "maintainers": ["Example <example@example.com>"],
            "capabilities": ["robonix/service/example"],
            "deploy_dependencies": [],
        }

        with tempfile.TemporaryDirectory() as tmp:
            public = Path(tmp)
            BUILD_CATALOG.render_site(public, "2026-07-14T00:00:00+00:00", [package])
            rendered = (public / "index.html").read_text()
            api_viewer = (public / "api" / "view" / "index.html").read_text()
            copied_mark = (public / "assets" / "robonix-mark.svg").read_bytes()

        self.assertIn("Find what your robot can run.", rendered)
        self.assertIn('id="catalogSearch"', rendered)
        self.assertIn('class="home-result"', rendered)
        self.assertIn('href="packages/?kind=primitive"', rendered)
        self.assertIn("searchCatalog()", rendered)
        self.assertIn("<summary>Catalog API</summary>", rendered)
        self.assertIn('href="api/view/"', rendered)
        self.assertIn('class="home-result-name"', rendered)
        self.assertNotIn('class="brand-bus"', rendered)
        self.assertIn("fetch(file)", api_viewer)
        self.assertIn("../v1/${resource}.json", api_viewer)
        self.assertIn("min-height: 100dvh", rendered)
        self.assertIn("flex-direction: column", rendered)
        self.assertIn('class="site-footer"', rendered)
        self.assertIn(".site-footer {", rendered)
        self.assertIn("max-width: none", rendered)
        self.assertIn("-webkit-backdrop-filter: blur(16px)", rendered)
        self.assertEqual(copied_mark, (ROOT / "assets" / "robonix-mark.svg").read_bytes())
        self.assertIn(
            '<link rel="icon" type="image/svg+xml" href="assets/robonix-mark.svg">',
            rendered,
        )
        self.assertIn(
            'class="brand-mark" src="assets/robonix-mark.svg" alt="" aria-hidden="true"',
            rendered,
        )
        self.assertIn(
            '<link rel="icon" type="image/svg+xml" href="../../assets/robonix-mark.svg">',
            api_viewer,
        )
        self.assertIn(
            'class="brand-mark" src="../../assets/robonix-mark.svg" alt="" aria-hidden="true"',
            api_viewer,
        )

    def test_catalog_workflow_supports_manual_refresh(self):
        workflow = (ROOT / ".github/workflows/catalog.yml").read_text()
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("python scripts/build_catalog.py", workflow)
        self.assertGreaterEqual(workflow.count("- assets/**"), 2)

    def test_api_writer_keeps_extensionless_resource_and_json_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "api" / "v1" / "catalog"
            BUILD_CATALOG.write_api(target, {"api_version": "1"})

            self.assertTrue(target.is_file())
            self.assertTrue(target.with_name("catalog.json").is_file())


class CatalogMetadataTests(unittest.TestCase):
    def test_missing_catalog_mapping_is_a_hard_failure(self):
        with self.assertRaises(SystemExit):
            BUILD_CATALOG.validate_catalog_metadata(
                "robonix.robot.example", None, "robonix.robot.example"
            )

    def test_license_is_preserved(self):
        metadata = {
            "name": "robonix.robot.example",
            "version": "0.1.0",
            "description": "Example robot",
            "license": "Apache-2.0",
            "tags": ["robot"],
            "maintainers": ["Example <example@example.com>"],
        }

        _, _, license_name, _, _ = BUILD_CATALOG.validate_catalog_metadata(
            "robonix.robot.example", metadata, "robonix.robot.example"
        )

        self.assertEqual(license_name, "Apache-2.0")

    def test_missing_license_for_new_entry_is_a_hard_failure(self):
        metadata = {
            "name": "robonix.robot.new",
            "version": "0.1.0",
            "description": "New robot",
            "tags": ["robot"],
            "maintainers": ["Example <example@example.com>"],
        }

        with self.assertRaises(SystemExit):
            BUILD_CATALOG.validate_catalog_metadata(
                "robonix.robot.new", metadata, "robonix.robot.new"
            )

    def test_preexisting_wheeltec_license_exception_uses_noassertion(self):
        metadata = {
            "name": "robonix.robot.wheeltec.r550",
            "version": "0.1.0",
            "description": "Legacy robot",
            "tags": ["robot"],
            "maintainers": ["Example <example@example.com>"],
        }

        _, _, license_name, _, _ = BUILD_CATALOG.validate_catalog_metadata(
            "robonix.robot.wheeltec.r550", metadata, "robonix.robot.wheeltec.r550"
        )

        self.assertEqual(license_name, "NOASSERTION")

    def test_empty_tags_is_a_hard_failure(self):
        metadata = {
            "name": "robonix.service.example",
            "version": "0.1.0",
            "description": "Example service",
            "license": "Apache-2.0",
            "tags": [],
            "maintainers": ["Example <example@example.com>"],
        }

        with self.assertRaises(SystemExit):
            BUILD_CATALOG.validate_catalog_metadata(
                "robonix.service.example", metadata, "robonix.service.example"
            )


if __name__ == "__main__":
    unittest.main()
