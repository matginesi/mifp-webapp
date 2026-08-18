from __future__ import annotations

import functools
from pathlib import Path

SCREENSHOT_DIR = Path("/tmp/mifp-browser-tests")


def login(page, base_url: str, username: str, password: str):
    page.goto(f"{base_url}/login")
    token = page.evaluate("""
        () => document.querySelector('input[name="_csrf_token"]')?.value || ""
    """)
    page.evaluate("""
        ({token, user, pw}) => {
            document.querySelector('input[name="login_username"]').value = user;
            document.querySelector('input[name="login_password"]').value = pw;
            document.querySelector('input[name="_csrf_token"]').value = token;
            document.querySelector('#loginForm').submit();
        }
    """, {"token": token, "user": username, "pw": password})
    page.wait_for_load_state("networkidle")


def csrf_token(page) -> str:
    return page.evaluate("""
        () => document.querySelector('input[name="_csrf_token"]')?.value
            || document.querySelector('meta[name="csrf-token"]')?.getAttribute('content')
            || ""
    """)


def chart_js_has_data(page, canvas_selector: str = "canvas") -> bool:
    return page.evaluate("""
        (selector) => {
            const canvases = document.querySelectorAll(selector);
            for (const c of canvases) {
                const chart = Chart.getChart(c);
                if (chart && chart.data && chart.data.datasets.length > 0)
                    return true;
            }
            return false;
        }
    """, canvas_selector)


def wait_for_chart_render(page, canvas_selector: str = "canvas", timeout: int = 5000):
    page.wait_for_function("""
        (selector) => {
            const canvases = document.querySelectorAll(selector);
            for (const c of canvases) {
                const chart = Chart.getChart(c);
                if (chart && chart.data && chart.data.datasets.length > 0)
                    return true;
            }
            return false;
        }
    """, arg=canvas_selector, timeout=timeout)


def screenshot_on_failure(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            for arg in list(args) + list(kwargs.values()):
                if hasattr(arg, "screenshot"):
                    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
                    path = str(SCREENSHOT_DIR / f"{func.__name__}.png")
                    arg.screenshot(path=path)
                    break
            raise
    return wrapper
