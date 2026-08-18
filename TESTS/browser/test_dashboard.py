from __future__ import annotations

import json
from pathlib import Path
import re
import zipfile

import pytest
from playwright.sync_api import expect

from .helpers import screenshot_on_failure

WEBAPP_DIR = Path(__file__).resolve().parents[2] / "MIFPAPP" / "CORE"

DASHBOARD_GET = [
    "/dashboard/",
    "/dashboard/stats",
    "/dashboard/server",
    "/dashboard/data-portability",
    "/dashboard/join-requests",
    "/dashboard/assets",
    "/dashboard/logs",
    "/dashboard/data-quality",
    "/dashboard/conferences",
    "/dashboard/control",
    "/dashboard/control/site",
    "/dashboard/control/backups",
    "/dashboard/control/processes",
    "/dashboard/control/quality",
    "/dashboard/control/incidents",
    "/dashboard/control/settings",
    "/dashboard/site-texts",
    "/dashboard/institutional",
    "/dashboard/institutional/privacy",
]

DASHBOARD_CONTENT = [
    "/dashboard/content/events",
    "/dashboard/content/news",
    "/dashboard/content/members",
    "/dashboard/content/sponsors",
    "/dashboard/content/publications",
    "/dashboard/content/research",
]


def _login(page, live_server):
    from .conftest import _admin_credentials
    from .helpers import login

    user, pw = _admin_credentials()
    login(page, live_server, user, pw)


class TestDashboardRoutes:

    @pytest.mark.parametrize("width,height", [(375, 812), (1440, 900)])
    @screenshot_on_failure
    def test_dashboard_visual_inventory(self, live_server, page, width, height):
        console_errors: list[str] = []
        page_errors: list[str] = []
        server_errors: list[str] = []
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on(
            "response",
            lambda response: server_errors.append(f"{response.status} {response.url}")
            if response.status >= 500 else None,
        )
        page.set_viewport_size({"width": width, "height": height})
        _login(page, live_server)
        captures = [
            ("index", "/dashboard/", None),
            ("content-list", "/dashboard/content/news", None),
            ("content-editor", "/dashboard/content/news", "[data-create-record-open]"),
            ("events", "/dashboard/events", None),
            ("event-wizard", "/dashboard/events", "[data-event-wizard='new']"),
            ("assets", "/dashboard/assets", None),
            ("import-export", "/dashboard/data-portability", None),
            ("data-quality", "/dashboard/data-quality", None),
            ("statistics", "/dashboard/stats", None),
            ("logs", "/dashboard/logs", None),
            ("server", "/dashboard/server", None),
        ]
        for name, route, trigger in captures:
            response = page.goto(f"{live_server}{route}")
            page.wait_for_load_state("networkidle")
            assert response is None or response.ok
            expect(page.locator("main").first).to_be_visible()
            if trigger:
                control = page.locator(trigger).first
                expect(control).to_be_visible()
                control.click()
                page.wait_for_timeout(500)
                modal = page.locator(".modal.show").first
                if modal.count():
                    expect(modal).to_be_visible()
                    assert modal.evaluate(
                        "(el) => Number(getComputedStyle(el).zIndex)"
                    ) > page.locator(".modal-backdrop").last.evaluate(
                        "(el) => Number(getComputedStyle(el).zIndex)"
                    )
            overflow = page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            assert overflow <= 1, f"{name} overflows by {overflow}px at {width}px"
            page.screenshot(
                path=f"/tmp/mifp-correction-{name}-{width}x{height}.png",
                full_page=True,
            )
        assert not console_errors, console_errors
        assert not page_errors, page_errors
        assert not server_errors, server_errors

    @pytest.mark.parametrize("width,height", [(320, 800), (375, 812), (768, 1024), (1024, 768), (1440, 900)])
    @screenshot_on_failure
    def test_responsive_shell_has_no_global_overflow(self, live_server, page, width, height):
        errors: list[str] = []
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.set_viewport_size({"width": width, "height": height})
        _login(page, live_server)
        for route in ("/dashboard/", "/dashboard/events", "/dashboard/assets", "/dashboard/data-portability"):
            page.goto(f"{live_server}{route}")
            page.wait_for_load_state("networkidle")
            overflow = page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            assert overflow <= 1, f"{route} overflows by {overflow}px at {width}px"
        page.screenshot(path=f"/tmp/mifp-dashboard-{width}x{height}.png", full_page=True)
        assert not errors, f"Console errors at {width}px: {errors}"

    @screenshot_on_failure
    def test_compact_shell_and_page_navigation(self, live_server, page):
        page.set_viewport_size({"width": 390, "height": 800})
        _login(page, live_server)
        page.goto(f"{live_server}/dashboard/")
        expect(page.locator(".dashboard-directory").first).to_be_visible()
        expect(page.locator(".dashboard-topbar")).to_be_visible()
        sidebar = page.locator("#dashboardSidebar")
        toggle = page.locator("[data-shell-toggle]")
        expect(sidebar).to_be_hidden()
        expect(toggle).to_have_attribute("aria-expanded", "false")
        toggle.click()
        expect(sidebar).to_be_visible()
        expect(toggle).to_have_attribute("aria-expanded", "true")
        page.wait_for_timeout(250)
        page.screenshot(path="/tmp/mifp-shell-mobile-open.png", full_page=False)
        members = sidebar.get_by_role("link", name="Members")
        expect(members).to_be_visible()
        members.click()
        page.wait_for_load_state("networkidle")
        expect(page.locator(".page-breadcrumb a", has_text="Dashboard")).to_be_visible()
        expect(page.locator("#dashboardSidebar")).to_be_hidden()
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 1

    @screenshot_on_failure
    def test_open_modal_and_toast_stay_inside_viewport_during_resize(self, live_server, page):
        _login(page, live_server)
        page.goto(f"{live_server}/dashboard/")
        page.wait_for_load_state("networkidle")
        page.evaluate("""() => {
          window.MIFPUI.showToast('Resize contract test notification', 'warning');
          bootstrap.Modal.getOrCreateInstance(document.getElementById('confirmDialog')).show();
        }""")
        expect(page.locator("#confirmDialog")).to_be_visible()
        # Bootstrap animates modal opacity and transform for 300 ms. Wait for
        # the stable geometry before asserting viewport bounds.
        page.wait_for_timeout(350)
        for width, height in ((1440, 900), (820, 520), (575, 700), (390, 700), (700, 430)):
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(120)
            modal_box = page.locator("#confirmDialog .modal-dialog").bounding_box()
            toast_box = page.locator("#toastContainer .toast-notification").first.bounding_box()
            assert modal_box is not None
            assert toast_box is not None
            assert modal_box["x"] >= -1
            assert modal_box["y"] >= -1
            assert modal_box["x"] + modal_box["width"] <= width + 1
            assert modal_box["y"] + modal_box["height"] <= height + 1
            assert toast_box["x"] >= -1
            assert toast_box["y"] >= -1
            assert toast_box["x"] + toast_box["width"] <= width + 1
            assert toast_box["y"] + toast_box["height"] <= height + 1

    @screenshot_on_failure
    def test_desktop_sidebar_collapses_and_persists(self, live_server, page):
        page.set_viewport_size({"width": 1440, "height": 900})
        _login(page, live_server)
        page.goto(f"{live_server}/dashboard/")
        page.evaluate("localStorage.removeItem('mifp-dashboard-sidebar-collapsed')")
        page.reload()
        shell = page.locator("[data-dashboard-shell]")
        toggle = page.locator("[data-shell-toggle]")
        assert not shell.evaluate("(element) => element.classList.contains('is-collapsed')")
        toggle.click()
        assert shell.evaluate("(element) => element.classList.contains('is-collapsed')")
        expect(toggle).to_have_attribute("aria-expanded", "false")
        page.reload()
        assert shell.evaluate("(element) => element.classList.contains('is-collapsed')")
        page.screenshot(path="/tmp/mifp-shell-desktop-collapsed.png", full_page=False)

    @pytest.mark.parametrize("route", DASHBOARD_GET)
    @screenshot_on_failure
    def test_dashboard_page_loads(self, live_server, page, route):
        _login(page, live_server)
        resp = page.goto(f"{live_server}{route}")
        page.wait_for_load_state("networkidle")
        assert resp is None or resp.ok

    @pytest.mark.parametrize("route", DASHBOARD_CONTENT)
    @screenshot_on_failure
    def test_dashboard_content_page_loads(self, live_server, page, route):
        _login(page, live_server)
        resp = page.goto(f"{live_server}{route}")
        page.wait_for_load_state("networkidle")
        assert resp is None or resp.ok

    @screenshot_on_failure
    def test_login_logout(self, live_server, page):
        console_errors: list[str] = []
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)

        _login(page, live_server)
        resp = page.goto(f"{live_server}/dashboard/")
        page.wait_for_load_state("networkidle")
        assert resp is None or resp.ok

        logout_btn = page.locator("form[action*='logout'] button[type='submit']")
        expect(logout_btn).to_have_count(1)
        logout_btn.click()
        expect(page.locator("#login-title")).to_be_visible(timeout=5000)
        assert page.url == f"{live_server}/login?logged_out=1"
        expect(page.get_by_text("You have been logged out.", exact=True)).to_be_visible()
        expect(page.locator("#pageLoader")).to_have_count(0)

        # A direct navigation after logout must still be rejected.
        page.goto(f"{live_server}/dashboard/")
        page.wait_for_load_state("networkidle")
        assert page.url.startswith(f"{live_server}/login")
        expect(page.locator("#login-title")).to_be_visible()
        assert not console_errors, console_errors

    @screenshot_on_failure
    def test_csrf_protection(self, live_server, page):
        _login(page, live_server)
        resp = page.request.post(
            f"{live_server}/dashboard/",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={},
        )
        assert resp.status == 400

    @screenshot_on_failure
    def test_dashboard_stats_chart(self, live_server, page):
        _login(page, live_server)
        page.goto(f"{live_server}/dashboard/stats")
        page.wait_for_load_state("networkidle")
        from .helpers import chart_js_has_data

        assert chart_js_has_data(page) or page.locator(".dash-empty-state, .empty").count() > 0

    @screenshot_on_failure
    def test_content_crud_create_event(self, live_server, page):
        _login(page, live_server)
        page.goto(f"{live_server}/dashboard/events")
        page.wait_for_load_state("networkidle")

        add_btn = page.locator("[data-event-wizard='new']")
        expect(add_btn).to_be_visible()
        add_btn.click()
        expect(page.locator("#eventWizard")).to_be_visible()

        title_input = page.locator("#eventWizardForm input[name='title']")
        expect(title_input).to_be_visible()
        test_title = f"Browser Test Event {__import__('time').time()}"
        title_input.fill(test_title)
        page.locator("#wizardNext").click()
        page.locator("#wizardNext").click()
        page.locator("#wizardSubmit").click()
        page.wait_for_load_state("networkidle")

        assert test_title in page.text_content("body")

    @pytest.mark.parametrize(
        "section,field,label",
        [
            ("members", "display_name", "Browser Action Member"),
            ("news", "title", "Browser Action News"),
            ("publications", "title", "Browser Action Publication"),
            ("sponsors", "name", "Browser Action Sponsor"),
            ("research", "title", "Browser Action Research"),
        ],
    )
    @screenshot_on_failure
    def test_content_create_actions(self, live_server, page, section, field, label):
        _login(page, live_server)
        page.goto(f"{live_server}/dashboard/content/{section}")
        page.wait_for_load_state("networkidle")
        page.locator("[data-create-record-open]").click()
        form = page.locator("[data-create-record-form]")
        expect(form).to_be_visible()
        form.locator(f"[name='{field}']").fill(label)
        form.locator("button[type='submit']").click()
        page.wait_for_load_state("networkidle")
        expect(page.locator("body")).to_contain_text(label)

    @screenshot_on_failure
    def test_data_portability_export(self, live_server, page):
        _login(page, live_server)
        page.goto(f"{live_server}/dashboard/data-portability")
        page.wait_for_load_state("networkidle")

        export_btn = page.locator("button[data-export='jsonl']")
        expect(export_btn).to_be_visible()
        export_btn.click()
        expect(page.locator("#exportAuthModal")).to_be_visible()
        page.locator("#exportAuthPassword").fill("browser-test-password")
        page.locator("#exportAuthSubmit").click()
        expect(page.locator("#transferResultTitle")).to_contain_text(
            "ready", timeout=30000
        )
        with page.expect_download(timeout=15000) as download_info:
            page.locator("#transferDownload").click()
        download = download_info.value
        assert download.suggested_filename.endswith(".jsonl")

    @screenshot_on_failure
    def test_data_portability_import_validation_action(self, live_server, page, tmp_path):
        payload = tmp_path / "browser-import.jsonl"
        payload.write_text(
            '{"type":"news","data":{"title":"Browser Import Validation","slug":"browser-import-validation","review_status":"draft"},"links":[],"assets":[],"meta":{}}\n',
            encoding="utf-8",
        )
        _login(page, live_server)
        page.goto(f"{live_server}/dashboard/data-portability")
        page.wait_for_load_state("networkidle")
        page.locator("#transferFiles").set_input_files(str(payload))
        expect(page.locator("#transferFileCount")).to_have_text("1 file")
        page.locator("#transferImportButton").click()
        expect(page.locator("#importAuthModal")).to_be_visible()
        page.locator("#importAuthPassword").fill("browser-test-password")
        page.locator("#importAuthSubmit").click()
        expect(page.locator("#transferModal")).to_be_visible()
        expect(page.locator("#transferResultTitle")).to_contain_text(
            re.compile("valid", re.IGNORECASE), timeout=30000
        )

    @screenshot_on_failure
    def test_data_portability_queues_multiple_zip_packages(self, live_server, page, tmp_path):
        packages = []
        for name in ("first.zip", "second.zip", "third.zip"):
            package = tmp_path / name
            with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    "manifest.json",
                    json.dumps({"scope": "all", "records": 0, "files": []}),
                )
                archive.writestr("records.jsonl", "")
            packages.append(str(package))

        _login(page, live_server)
        page.goto(f"{live_server}/dashboard/data-portability")
        page.wait_for_load_state("networkidle")
        page.locator("#transferFiles").set_input_files(packages)

        expect(page.locator("#transferFileCount")).to_have_text("3 files")
        expect(page.locator("#transferBatchNotice")).to_contain_text(
            "3 sequential uploads"
        )
        page.locator("#transferImportButton").click()
        expect(page.locator("#importAuthModal")).to_be_visible()
        page.locator("#importAuthPassword").fill("browser-test-password")
        page.locator("#importAuthSubmit").click()
        expect(page.locator("#transferResultTitle")).to_contain_text(
            "Validation complete", timeout=30000
        )
        expect(page.locator("#transferResultMessage")).to_contain_text(
            "All 3 queued uploads completed successfully"
        )

    @screenshot_on_failure
    def test_asset_picker_opens(self, live_server, page):
        _login(page, live_server)
        resp = page.goto(f"{live_server}/dashboard/assets")
        page.wait_for_load_state("networkidle")
        assert resp is None or resp.ok

        add_btn = page.locator(
            "a[href*='upload'], button[data-action='upload'], a:has-text('Upload'), a:has-text('Add Asset')"
        )
        if add_btn.count() > 0:
            add_btn.first.click()
            page.wait_for_timeout(500)
            upload_form = page.locator("#uploadForm.show, #uploadForm.collapsing")
            if upload_form.count() > 0:
                expect(upload_form.first).to_be_visible(timeout=3000)

    @screenshot_on_failure
    def test_asset_upload_action(self, live_server, page):
        _login(page, live_server)
        page.goto(f"{live_server}/dashboard/assets")
        page.wait_for_load_state("networkidle")
        page.locator("[data-bs-target='#assetCreateModal']").click()
        expect(page.locator("#assetCreateModal")).to_be_visible()
        page.locator("#assetUploadForm input[name='file']").set_input_files(
            str(WEBAPP_DIR / "mifp_app/static/img/logo-mifp.png")
        )
        page.locator("#assetUploadForm input[name='alt_text']").fill("Browser test logo")
        page.locator("#assetCreateSubmit").click()
        page.wait_for_load_state("networkidle")
        expect(page.locator("body")).to_contain_text("Asset uploaded")

    @screenshot_on_failure
    def test_event_sections_and_cover_upload(self, live_server, page):
        errors: list[str] = []
        failed_responses: list[str] = []
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.on(
            "response",
            lambda response: failed_responses.append(f"{response.status} {response.url}")
            if response.status >= 400 else None,
        )
        _login(page, live_server)
        page.goto(f"{live_server}/dashboard/events")
        page.wait_for_load_state("networkidle")

        expect(page.get_by_role("heading", name="Current and upcoming events")).to_be_visible()
        expect(page.get_by_role("heading", name="Past events")).to_be_visible()

        page.locator("[data-event-wizard='new']").click()
        expect(page.locator("#eventWizard")).to_be_visible()
        page.locator("#eventWizardForm input[name='title']").fill("Browser Cover Event")
        page.locator("#wizardNext").click()
        page.locator("#wizardNext").click()
        page.locator("#wizardCoverInput").set_input_files(
            str(WEBAPP_DIR / "mifp_app/static/img/logo-mifp.png")
        )
        expect(page.locator("#wizardCoverPreview")).to_be_visible(timeout=10000)
        expect(page.locator("#wizardCoverAssetId")).not_to_have_value("")
        assert not errors, errors
        assert not failed_responses, failed_responses

    @screenshot_on_failure
    def test_asset_picker_reports_expired_session_in_ui_and_console(self, live_server, page):
        errors: list[str] = []
        page.on(
            "console",
            lambda message: errors.append(message.text)
            if message.type == "error" and "401 (UNAUTHORIZED)" not in message.text
            else None,
        )
        _login(page, live_server)
        page.goto(f"{live_server}/dashboard/assets")
        page.wait_for_load_state("networkidle")
        page.evaluate(
            """
            async () => {
              const token = document.querySelector('meta[name="csrf-token"]').content;
              await fetch('/logout', {method: 'POST', headers: {'X-CSRF-Token': token}});
            }
            """
        )
        page.evaluate("new bootstrap.Modal(document.getElementById('assetPickerModal')).show()")
        page.locator("#assetPickerSearchBtn").click()
        expect(page.locator("#assetPickerResults")).to_contain_text(
            "session has expired", timeout=5000
        )
        assert len(errors) == 1, errors
        assert '"event":"api.request_failed"' in errors[0]
        assert "session has expired" in errors[0].lower()

    @screenshot_on_failure
    def test_dashboard_runtime_dependencies_are_local(self, live_server, page):
        external_requests: list[str] = []

        def record_request(request):
            url = request.url
            if not (url.startswith(live_server) or url.startswith(("data:", "blob:"))):
                external_requests.append(url)

        page.on("request", record_request)
        _login(page, live_server)
        for route in ("/dashboard/", "/dashboard/events", "/dashboard/assets", "/dashboard/stats"):
            page.goto(f"{live_server}{route}")
            page.wait_for_load_state("networkidle")

        assert not external_requests, external_requests

    @screenshot_on_failure
    def test_server_panel(self, live_server, page):
        _login(page, live_server)
        resp = page.goto(f"{live_server}/dashboard/server")
        page.wait_for_load_state("networkidle")
        assert resp is None or resp.ok

        from .helpers import csrf_token

        token = csrf_token(page)
        assert token, "No CSRF token found"
        resp = page.request.post(
            f"{live_server}/dashboard/server/integrity-check",
            data={"_csrf_token": token},
        )
        assert resp.status in (200, 302, 400)

    @screenshot_on_failure
    def test_conference_site_wizard_and_deploy_download(self, live_server, page):
        _login(page, live_server)
        page.goto(f"{live_server}/dashboard/conferences")
        page.get_by_role("button", name="New conference").click()
        modal = page.locator("#newConference")
        modal.locator('[name="title"]').fill("Browser Physics 2028")
        modal.locator('[name="acronym"]').fill("BP28")
        modal.locator('[name="year"]').fill("2028")
        modal.locator('[name="slug"]').fill("browser-physics-2028")
        modal.get_by_role("button", name="Next").click()
        expect(modal.locator('[data-conference-step-marker="2"].is-active')).to_contain_text("Dates & URL")
        modal.locator('[name="city"]').fill("Rome")
        modal.get_by_role("button", name="Next").click()
        expect(modal.locator('[data-conference-step-marker="3"].is-active')).to_contain_text("Files & import")
        modal.get_by_role("button", name="Create conference").click()
        page.wait_for_url("**/dashboard/conferences/*")
        expect(page.get_by_role("heading", name="Identity and public URL")).to_be_visible()
        expect(page.get_by_role("heading", name="Complete website configuration")).to_be_visible()
        page.locator('[name="canonical_url"]').fill("https://events.example.org/bp28/")
        page.locator('[name="deploy_base_path"]').fill("/bp28/")
        page.get_by_role("button", name="Save conference details").click()
        page.wait_for_load_state("networkidle")
        page.locator('[name="config__deployment__nginx_base_path"]').fill("/bp28/")
        page.locator('[name="config__appearance__default_mode"]').select_option("light")
        page.locator('[name="config__runtime__console_log_level"]').select_option("warn")
        page.get_by_role("button", name="Save config.yaml settings").click()
        page.wait_for_load_state("networkidle")
        expect(page.locator('[name="config__deployment__nginx_base_path"]')).to_have_value("/bp28")
        expect(page.locator('[name="config__appearance__default_mode"]')).to_have_value("light")
        with page.expect_download() as download_info:
            page.get_by_role("link", name="Build deploy ZIP").first.click()
        assert download_info.value.suggested_filename == "browser-physics-2028-deploy.zip"
        page.goto(f"{live_server}/dashboard/conferences")
        expect(page.get_by_role("table")).to_be_visible()
        row = page.get_by_role("row").filter(has_text="Browser Physics 2028")
        expect(row).to_be_visible()
        page.set_viewport_size({"width": 390, "height": 844})
        expect(row.get_by_role("button", name="Edit")).to_be_visible()
        expect(row.get_by_role("button", name="Delete")).to_be_visible()
        row.get_by_role("button", name="Edit").click()
        edit_modal = page.locator(".modal.show")
        expect(edit_modal.get_by_role("heading", name="Edit BP28")).to_be_visible()
        edit_modal.get_by_role("button", name="Close").click()
        row.get_by_role("button", name="Delete").click()
        delete_modal = page.locator(".modal.show")
        expect(delete_modal.get_by_role("heading", name="Delete conference")).to_be_visible()
        delete_modal.get_by_role("button", name="Delete conference and storage").click()
        page.wait_for_url("**/dashboard/conferences")
        expect(page.get_by_text("Browser Physics 2028", exact=True)).to_have_count(0)
