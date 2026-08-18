from __future__ import annotations

import pytest
from playwright.sync_api import expect

from .helpers import chart_js_has_data, screenshot_on_failure, wait_for_chart_render

PUBLIC_ROUTES = [
    ("/", ["nav", "main", "footer"]),
    ("/events", ["main"]),
    ("/news", ["main"]),
    ("/publications", ["main", "#pubSearch"]),
    ("/about", ["main"]),
    ("/research", ["main", "canvas"]),
    ("/members", ["main"]),
    ("/manifesto", ["main"]),
    ("/privacy", ["main"]),
    ("/cookie-policy", ["main"]),
    ("/code-of-conduct", ["main"]),
    ("/sponsors", ["main"]),
    ("/sponsors/how-to-become-a-sponsor", ["main"]),
    ("/join", ["main", "form"]),
]

PUBLIC_404_OK = [
    "/events/smoke-test-dummy-slug",
    "/news/smoke-test-dummy-slug",
    "/sponsors/smoke-test-dummy-slug",
    "/media/nonexistent.png",
]

PUBLIC_FILE_ROUTES = [
    "/pdf/research",
    "/pdf/about",
    "/pdf/manifesto",
    "/pdf/privacy",
    "/pdf/code-of-conduct",
    "/pdf/cookie-policy",
]


class TestPublicRoutes:

    @pytest.mark.parametrize("route,selectors", PUBLIC_ROUTES)
    @screenshot_on_failure
    def test_public_page_loads(self, live_server, page, route, selectors):
        page.goto(f"{live_server}{route}")
        page.wait_for_load_state("networkidle")
        assert page.url.rstrip("/").endswith(route.rstrip("/")) or page.url.rstrip("/").endswith(
            route.rstrip("/") + ".html"
        )
        for sel in selectors:
            if route == "/research" and sel == "canvas" and page.locator(sel).count() == 0:
                expect(page.locator("main .empty-state, main article").first).to_be_visible()
                continue
            expect(page.locator(sel).first).to_be_visible()

    @pytest.mark.parametrize("route", PUBLIC_404_OK)
    def test_public_404_ok(self, live_server, page, route):
        resp = page.goto(f"{live_server}{route}")
        page.wait_for_load_state("networkidle")
        assert resp.ok or resp.status == 404

    @pytest.mark.parametrize("route", PUBLIC_FILE_ROUTES)
    def test_public_file_served(self, live_server, page, route):
        resp = page.request.get(f"{live_server}{route}")
        assert resp.status == 200
        assert resp.headers["content-type"] in (
            "application/pdf",
            "text/html; charset=utf-8",
        )

    @screenshot_on_failure
    def test_research_chart_js_renders(self, live_server, page):
        page.goto(f"{live_server}/research")
        page.wait_for_load_state("networkidle")
        if page.locator("canvas").count():
            wait_for_chart_render(page)
            assert chart_js_has_data(page)
        else:
            expect(page.locator("main .empty-state, main article").first).to_be_visible()

    @screenshot_on_failure
    def test_sponsor_lightbox(self, live_server, page):
        page.goto(f"{live_server}/sponsors")
        page.wait_for_load_state("networkidle")
        sponsor_cards = page.locator(".sponsor-card-button, .js-sponsor-modal")
        count = sponsor_cards.count()
        assert count > 0, "Seeded sponsor card is missing"
        sponsor_cards.first.click()
        modal = page.locator(".mifp-lightbox.is-open")
        expect(modal.first).to_be_visible(timeout=3000)
        close_btn = page.locator(".mifp-lightbox-close")
        if close_btn.count() > 0:
            close_btn.first.click()
            expect(modal.first).not_to_be_visible(timeout=3000)

    @screenshot_on_failure
    def test_publications_filter(self, live_server, page):
        page.goto(f"{live_server}/publications")
        page.wait_for_load_state("networkidle")
        search = page.locator("#pubSearch")
        assert search.count() > 0, "Publication search control is missing"
        items_before = page.locator(".pub-card").count()
        search.fill("XYZZY_NONEXISTENT")
        page.wait_for_timeout(500)
        items_after = page.locator(".pub-card").count()
        assert items_after <= items_before

    @screenshot_on_failure
    def test_cookie_banner(self, live_server, page):
        from .conftest import _admin_credentials
        from .helpers import csrf_token, login

        user, password = _admin_credentials()
        login(page, live_server, user, password)
        token = csrf_token(page)
        assert token
        response = page.request.post(
            f"{live_server}/dashboard/institutional/privacy",
            form={
                "_csrf_token": token,
                "_action": "save_banner",
                "cookie_banner_enabled": "1",
                "cookie_banner_text": "Browser test cookie notice.",
                "cookie_banner_link_enabled": "1",
                "cookie_banner_dismiss_label": "Dismiss",
                "cookie_banner_theme": "brand",
            },
        )
        assert response.ok
        page.goto(f"{live_server}/")
        page.wait_for_load_state("networkidle")
        banner = page.locator("#cookie-banner")
        assert banner.count() > 0, "Cookie banner is missing"
        expect(banner.first).to_be_visible()
        dismiss = page.locator("#cookie-banner-close")
        expect(dismiss).to_be_visible()
        dismiss.click()
        expect(banner.first).not_to_be_visible(timeout=3000)

    @screenshot_on_failure
    def test_navbar_responsive(self, live_server, page):
        page.set_viewport_size({"width": 480, "height": 800})
        page.goto(f"{live_server}/")
        page.wait_for_load_state("networkidle")
        toggler = page.locator("#navToggle")
        expect(toggler).to_be_visible()
        nav_collapse = page.locator("#mobileMenu")
        expect(toggler).to_have_attribute("aria-expanded", "false")
        toggler.first.click()
        expect(nav_collapse).to_be_visible()
        expect(toggler).to_have_attribute("aria-expanded", "true")
        page.keyboard.press("Escape")
        expect(nav_collapse).not_to_be_visible()
        expect(toggler).to_have_attribute("aria-expanded", "false")

    @pytest.mark.parametrize("width", [360, 390])
    def test_home_has_no_page_horizontal_overflow(self, live_server, page, width):
        page.set_viewport_size({"width": width, "height": 800})
        page.goto(f"{live_server}/")
        page.wait_for_load_state("networkidle")
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 1
