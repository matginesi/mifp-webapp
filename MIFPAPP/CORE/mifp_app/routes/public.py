from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    make_response,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from werkzeug.exceptions import HTTPException

from ..db.connection import connect, connect_readonly
from ..services.mailer import send_mail
from ..services.metrics_service import classify_asset_key, increment_daily
from ..services.public_repository import (
    NEWS_TYPE_LABELS,
    get_home_context,
    get_public_event,
    get_public_news,
    get_public_page,
    get_public_page_by_slug,
    get_public_sponsor,
    list_home_sponsors,
    list_members_page,
    list_news_page,
    list_public_events,
    list_public_publications,
    list_public_research,
    sanitize_html,
    sitemap_dynamic_entries,
)
from ..utils.logger import audit_log, security_event
from ..utils.security import get_client_ip, ip_rate_allowed

bp = Blueprint("public", __name__)

# ---------------------------------------------------------------------------
# Markdown helper for institutional pages
# ---------------------------------------------------------------------------

_MD_DIR = Path(__file__).resolve().parent.parent.parent  # MIFPAPP/CORE/

def _markdown_html(raw: str | None) -> str | None:
    try:
        import markdown as md_lib
    except ImportError:
        return raw
    if not raw:
        return None
    return sanitize_html(md_lib.markdown(raw, extensions=["fenced_code", "tables", "nl2br"]))


def _increment_metric(scope: str, metric_name: str, metric_key: str) -> None:
    if not current_app.config.get("PRIVACY_SAFE_METRICS_ENABLED", True):
        return
    try:
        with connect(current_app.config["DATABASE_PATH"]) as conn:
            increment_daily(conn, scope, metric_name, metric_key)
            conn.commit()
    except Exception:
        current_app.logger.debug("privacy-safe metric increment failed", exc_info=True)


def _alias_target(conn, entity_type: str, slug: str) -> str | None:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='content_aliases'").fetchone():
        return None
    row = conn.execute(
        "SELECT canonical_slug FROM content_aliases WHERE entity_type=? AND old_slug=?",
        (entity_type, slug),
    ).fetchone()
    return str(row["canonical_slug"]) if row else None


def _render_md(filename: str) -> tuple[str | None, str | None]:
    """Read a .md file and return (html, raw_text) or (None, None)."""
    md_path = _MD_DIR / filename
    if not md_path.exists():
        return None, None
    raw = md_path.read_text(encoding="utf-8")
    html = _markdown_html(raw)
    return html, raw


def _page_or_md_html(page: dict | None, fallback_filename: str) -> str | None:
    if page and page.get("body"):
        return _markdown_html(page.get("body"))
    html, _ = _render_md(fallback_filename)
    return html


# ---------------------------------------------------------------------------
# Media – serve uploaded assets without login
# ---------------------------------------------------------------------------

@bp.get("/media/<path:filename>")
def media(filename: str):
    assets_dir: Path = current_app.config["ASSETS_DIR"]
    safe = Path(filename)
    if safe.is_absolute() or ".." in safe.parts:
        security_event("media.path_traversal", "path traversal attempt on media", severity="warning",
                       ip=request.remote_addr, filename=filename)
        return Response("Invalid filename", status=400)
    try:
        assets_root = assets_dir.resolve()
        target = (assets_root / safe).resolve()
        target.relative_to(assets_root)
    except (OSError, ValueError):
        security_event("media.path_traversal", "media target escaped assets root", severity="warning",
                       ip=request.remote_addr, filename=filename)
        return Response("Invalid filename", status=400)
    if not target.is_file():
        abort(404)
    response = send_from_directory(str(assets_dir), filename)
    response.headers.setdefault("Cache-Control", "public, max-age=3600")
    if filename.lower().endswith(".pdf") or filename.lower().endswith(".svg"):
        response.headers["Content-Disposition"] = "attachment"
    _increment_metric("public_download", "download", classify_asset_key(filename))
    return response


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------

@bp.get("/")
def home():
    db_path = current_app.config["DATABASE_PATH"]
    with connect_readonly(db_path) as conn:
        context = get_home_context(conn, lambda filename: url_for("public.media", filename=filename))
        research_areas = list_public_research(conn, lambda filename: url_for("public.media", filename=filename))

    return render_template(
        "public/home.html",
        forthcoming_events=context["forthcoming_events"],
        news=context["news"],
        manifesto=context["manifesto"],
        news_type_labels=NEWS_TYPE_LABELS,
        total_events=context["total_events"],
        total_news=context["total_news"],
        member_count=context["member_count"],
        country_count=context["country_count"],
        sponsors=context["sponsors"],
        sponsor_how_to=context["sponsor_how_to"],
        research_areas=research_areas,
    )


@bp.get("/favicon.ico")
def favicon():
    static_dir = Path(current_app.static_folder or "")
    icon_path = static_dir / "img" / "logo-mifp.png"
    if not icon_path.is_file():
        abort(404)
    return send_from_directory(str(icon_path.parent), icon_path.name, mimetype="image/png")


def _canonical_url() -> str:
    """Return the canonical URL without 'www.' prefix."""
    host = request.host_url.rstrip('/')
    if host.startswith('www.'):
        return host[4:]
    return host


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

@bp.get("/events")
def events():
    db_path = current_app.config["DATABASE_PATH"]
    with connect(db_path) as conn:
        upcoming, past = list_public_events(conn, lambda filename: url_for("public.media", filename=filename))
    return render_template("public/events.html", upcoming=upcoming, past=past)


@bp.get("/events/<slug>")
@bp.get("/events/<slug>/")
def event_detail(slug: str):
    db_path = current_app.config["DATABASE_PATH"]
    with connect(db_path) as conn:
        if target := _alias_target(conn, "event", slug):
            return redirect(url_for("public.event_detail", slug=target), code=308)
        event = get_public_event(conn, slug, lambda filename: url_for("public.media", filename=filename, _external=True))
        if not event:
            abort(404)
    event["conference_website_url"] = event.get("remote_url")
    return render_template("public/event_detail.html", event=event)


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------

@bp.get("/news")
def news():
    db_path = current_app.config["DATABASE_PATH"]
    news_type = request.args.get("type", "").strip() or None
    year = request.args.get("year", "").strip() or None
    content = request.args.get("content", "").strip() or None
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 12
    search = request.args.get("q", "").strip() or None
    with connect(db_path) as conn:
        result = list_news_page(
            conn,
            lambda filename: url_for("public.media", filename=filename),
            news_type,
            search,
            page,
            per_page,
            year=year,
            content_filter=content,
        )
    return render_template(
        "public/news.html",
        news=result["news"],
        types=result["types"],
        years=result["years"],
        current_type=news_type,
        current_year=year,
        current_content=content,
        news_type_labels=NEWS_TYPE_LABELS,
        page=page,
        total_pages=result["total_pages"],
        total=result["total"],
        search=search,
    )


@bp.get("/news/<slug>")
def news_detail(slug: str):
    db_path = current_app.config["DATABASE_PATH"]
    with connect(db_path) as conn:
        if target := _alias_target(conn, "news", slug):
            return redirect(url_for("public.news_detail", slug=target), code=308)
        article = get_public_news(conn, slug, lambda filename: url_for("public.media", filename=filename, _external=True))
        if not article:
            abort(404)
    return render_template("public/news_detail.html", article=article)


@bp.get("/publications")
def publications():
    db_path = current_app.config["DATABASE_PATH"]
    with connect(db_path) as conn:
        pubs = list_public_publications(conn, lambda filename: url_for("public.media", filename=filename))
    return render_template("public/publications.html", publications=pubs)


# ---------------------------------------------------------------------------
# Static pages
# ---------------------------------------------------------------------------

@bp.get("/about")
def about():
    db_path = current_app.config["DATABASE_PATH"]
    with connect(db_path) as conn:
        stats = {
            "members": conn.execute("SELECT COUNT(*) FROM members WHERE is_active=1").fetchone()[0],
            "events": conn.execute("SELECT COUNT(*) FROM events WHERE review_status='published'").fetchone()[0],
            "news": conn.execute("SELECT COUNT(*) FROM news WHERE review_status='published'").fetchone()[0],
            "publications": conn.execute("SELECT COUNT(*) FROM publications WHERE review_status='published'").fetchone()[0],
            "research_areas": conn.execute("SELECT COUNT(*) FROM research_areas WHERE review_status='published'").fetchone()[0],
            "sponsors": conn.execute("SELECT COUNT(*) FROM sponsors WHERE is_active=1").fetchone()[0],
        }
    about_html, _ = _render_md("About.md")
    return render_template("public/about.html", about_html=about_html, stats=stats)


_PDF_PAGES = {
    "about":           ("about",           "About.md",           "About MIFP"),
    "manifesto":       ("manifesto",       "Manifesto.md",       "Manifesto of Solidarity"),
    "privacy":         ("privacy",         "Privacy.md",         "Privacy Policy"),
    "code-of-conduct": ("code-of-conduct", "CodeOfConduct.md",   "Code of Conduct"),
    "cookie-policy":   ("cookie-policy",   "cookie-policy.md",   "Cookie Policy"),
    "sponsors-how-to": ("sponsors-how-to", "HowToBecomeASponsor.md", "How to Become a Sponsor"),
}

@bp.get("/pdf/<page_name>")
def pdf_page(page_name: str):
    if page_name not in _PDF_PAGES:
        abort(404)
    slug, fallback_md, title = _PDF_PAGES[page_name]
    content_html, _ = _render_md(fallback_md)
    if not content_html:
        abort(404)
    try:
        import weasyprint
    except ImportError:
        return Response("PDF generation not available", status=501)
    try:
        html = render_template("public/pdf_page.html",
            title=title,
            content_html=content_html,
            today=date.today().isoformat(),
            source_url=request.url_root.rstrip("/"))
        pdf_bytes = weasyprint.HTML(string=html, base_url=request.url_root).write_pdf()
    except Exception:
        current_app.logger.exception("PDF generation failed")
        abort(500)
    response = make_response(pdf_bytes)
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f'attachment; filename="{slug}.pdf"'
    return response


@bp.get("/research")
def research():
    db_path = current_app.config["DATABASE_PATH"]
    with connect(db_path) as conn:
        areas = list_public_research(conn, lambda filename: url_for("public.media", filename=filename))
        pub_by_year = [
            dict(r) for r in conn.execute(
                "SELECT COALESCE(year,'Unknown') AS year, COUNT(*) AS total FROM publications WHERE review_status='published' GROUP BY year ORDER BY year"
            ).fetchall()
        ]
        members_by_country = [
            dict(r) for r in conn.execute(
                "SELECT COALESCE(country,'Unknown') AS country, COUNT(*) AS total FROM members WHERE is_active=1 GROUP BY country ORDER BY total DESC LIMIT 10"
            ).fetchall()
        ]
        member_profile = conn.execute(
            """
            SELECT COUNT(*) AS active_members,
                   COUNT(DISTINCT NULLIF(TRIM(country), '')) AS represented_countries
            FROM members
            WHERE is_active=1
            """
        ).fetchone()
        research_stats = {
            "areas": len(areas),
            "publications": sum(int(row["total"]) for row in pub_by_year),
            "active_members": int(member_profile["active_members"] or 0),
            "countries": int(member_profile["represented_countries"] or 0),
        }
    return render_template(
        "public/research.html",
        research_areas=areas,
        pub_by_year=pub_by_year,
        members_by_country=members_by_country,
        research_stats=research_stats,
    )


@bp.get("/pdf/research")
def pdf_research():
    db_path = current_app.config["DATABASE_PATH"]
    try:
        with connect(db_path) as conn:
            areas = list_public_research(conn, lambda fn: url_for("public.media", filename=fn, _external=True))
    except HTTPException:
        raise
    except Exception:
        current_app.logger.exception("research PDF data fetch failed")
        abort(500)
    try:
        import weasyprint
    except ImportError:
        return Response("PDF generation not available", status=501)
    try:
        content_html = render_template("public/_research_pdf_content.html", research_areas=areas)
        html = render_template(
            "public/pdf_page.html",
            title="Research Areas",
            content_html=content_html,
            today=date.today().isoformat(),
            source_url=request.url_root.rstrip("/"),
        )
        pdf_bytes = weasyprint.HTML(string=html, base_url=request.url_root).write_pdf()
    except Exception:
        current_app.logger.exception("research PDF generation failed")
        abort(500)
    response = make_response(pdf_bytes)
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = 'attachment; filename="research.pdf"'
    return response


@bp.get("/members")
def members():
    db_path = current_app.config["DATABASE_PATH"]
    search = request.args.get("q", "").strip() or None
    role_filter = request.args.get("role", "").strip() or None
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 12

    with connect(db_path) as conn:
        result = list_members_page(conn, lambda filename: url_for("public.media", filename=filename), search, role_filter, page, per_page)

    return render_template(
        "public/members.html",
        members=result["members"],
        roles=result["roles"],
        search=search,
        role_filter=role_filter,
        page=page,
        total_pages=result["total_pages"],
        total=result["total"],
    )


@bp.get("/manifesto")
def manifesto():
    md_html, _ = _render_md("Manifesto.md")
    return render_template("public/manifesto.html", md_html=md_html)


@bp.get("/privacy")
def privacy():
    md_html, _ = _render_md("Privacy.md")
    return render_template("public/privacy.html", md_html=md_html)


@bp.get("/cookie-policy")
def cookie_policy():
    md_html, _ = _render_md("cookie-policy.md")
    return render_template("public/cookie_policy.html", md_html=md_html)


@bp.get("/code-of-conduct")
def code_of_conduct():
    md_html, _ = _render_md("CodeOfConduct.md")
    return render_template("public/code_of_conduct.html", md_html=md_html)


@bp.get("/sponsors")
def sponsors():
    db_path = current_app.config["DATABASE_PATH"]
    with connect(db_path) as conn:
        sponsors_list = list_home_sponsors(conn, lambda filename: url_for("public.media", filename=filename))
        sponsor_how_to = get_public_page_by_slug(conn, "sponsors-how-to") or get_public_page(conn, "sponsor")
    return render_template("public/sponsors.html", sponsors=sponsors_list, sponsor_how_to=sponsor_how_to)


@bp.get("/sponsors/<slug>")
def sponsor_detail(slug: str):
    db_path = current_app.config["DATABASE_PATH"]
    with connect(db_path) as conn:
        if target := _alias_target(conn, "sponsor", slug):
            return redirect(url_for("public.sponsor_detail", slug=target), code=308)
        sponsor = get_public_sponsor(conn, slug, lambda filename: url_for("public.media", filename=filename))
        if not sponsor:
            abort(404)
    return render_template("public/sponsor_detail.html", sponsor=sponsor)


@bp.get("/sponsors/how-to-become-a-sponsor")
def sponsor_how_to():
    db_path = current_app.config["DATABASE_PATH"]
    with connect(db_path) as conn:
        page = get_public_page_by_slug(conn, "sponsors-how-to") or get_public_page(conn, "sponsor")
    md_html = _page_or_md_html(page, "HowToBecomeASponsor.md")
    return render_template("public/sponsor_how_to.html", page=None if md_html else page, md_html=md_html)


# ---------------------------------------------------------------------------
# Join MIFP
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^@\s]{1,120}@[^@\s]{1,180}\.[^@\s]{2,40}$")


def _clean_text(name: str, max_len: int, required: bool = False) -> str:
    value = " ".join((request.form.get(name, "") or "").replace("\x00", "").split())
    value = value[:max_len]
    if required and not value:
        raise ValueError("Missing required field")
    return value


def _join_rate_limited(ip: str) -> bool:
    return not ip_rate_allowed(
        "join",
        ip,
        limit=int(current_app.config.get("JOIN_MAX_PER_IP_HOUR", 5)),
        window_seconds=3600,
    )


@bp.route("/join", methods=["GET", "POST"])
def join():
    db_path = current_app.config["DATABASE_PATH"]
    errors: list[str] = []
    success = False
    form = {}

    if request.method == "POST":
        ip = get_client_ip()
        if request.form.get("website", "").strip():
            security_event("join.honeypot_triggered", "Join form honeypot triggered", severity="warning", ip=ip)
            success = True
            return render_template("public/join.html", errors=[], success=success, form={})
        if _join_rate_limited(ip):
            errors.append("Too many requests from this network. Please try again later or contact MIFP administration.")
        else:
            try:
                form = {
                    "first_name": _clean_text("first_name", 80, True),
                    "last_name": _clean_text("last_name", 80, True),
                    "email": _clean_text("email", 180, True).lower(),
                    "affiliation": _clean_text("affiliation", 180),
                    "country": _clean_text("country", 80),
                    "field": _clean_text("field", 160),
                    "position": _clean_text("position", 120),
                    "motivation": (request.form.get("motivation", "") or "").strip()[:2500],
                }
                if not _EMAIL_RE.match(form["email"]):
                    errors.append("Please enter a valid email address.")
                with connect(db_path) as conn:
                    duplicate = conn.execute(
                        "SELECT id, status FROM join_requests WHERE lower(email)=lower(?) AND status IN ('pending','in_review') LIMIT 1",
                        (form["email"],),
                    ).fetchone()
                    if duplicate:
                        errors.append("A request with this email is already pending review.")
                    if not errors:
                        conn.execute(
                            """
                            INSERT INTO join_requests(
                                first_name,last_name,email,affiliation,country,field,position,orcid,website_url,
                                motivation,invitation_code,status,source_ip,user_agent
                            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                            """,
                            (
                                form["first_name"], form["last_name"], form["email"], form["affiliation"] or None,
                                form["country"] or None, form["field"] or None, form["position"] or None,
                                None, None, form["motivation"] or None,
                                None, "pending",
                                ip[:64] if current_app.config.get("JOIN_STORE_RAW_IP", False) else None,
                                None,
                            ),
                        )
                        conn.commit()
                        request_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                        audit_log(
                            "join.submitted", "Join request submitted", category="join",
                            entity_type="join_request", entity_id=request_id,
                        )
                        try:
                            send_mail(
                                current_app,
                                to=current_app.config.get("MAIL_TO", "info@mifp.eu"),
                                subject="New MIFP Join request",
                                reply_to=form["email"],
                                body=(
                                    f"New Join request\n\n"
                                    f"Name: {form['first_name']} {form['last_name']}\n"
                                    f"Email: {form['email']}\n"
                                    f"Affiliation: {form['affiliation']}\n"
                                    f"Country: {form['country']}\n"
                                    f"Field: {form['field']}\n\n"
                                    f"Motivation:\n{form['motivation']}\n"
                                ),
                            )
                        except Exception:
                            current_app.logger.exception("join notification email failed")
                        success = True
                        form = {}
            except ValueError:
                errors.append("Please check the submitted fields.")

    return render_template("public/join.html", errors=errors, error=(errors[0] if errors else None), success=success, form=form)


# ---------------------------------------------------------------------------
# SEO
# ---------------------------------------------------------------------------

@bp.get("/robots.txt")
def robots_txt():
    txt = (
        "User-agent: *\n"
        "Allow: /\n"
        f"Sitemap: {url_for('public.sitemap_xml', _external=True)}\n"
    )
    resp = make_response(txt, 200)
    resp.headers["Content-Type"] = "text/plain; charset=utf-8"
    return resp


@bp.get("/sitemap.xml")
def sitemap_xml():
    db_path = current_app.config["DATABASE_PATH"]
    today = date.today().isoformat()
    urls = []
    for endpoint in ("public.home", "public.events", "public.news", "public.publications", "public.research", "public.about", "public.privacy", "public.cookie_policy", "public.manifesto", "public.members", "public.code_of_conduct", "public.sponsors", "public.sponsor_how_to"):
        urls.append({"loc": url_for(endpoint, _external=True), "lastmod": today, "changefreq": "weekly", "priority": "0.8"})
    with connect(db_path) as conn:
        for row in sitemap_dynamic_entries(conn):
            endpoint = "public.event_detail" if row["kind"] == "event" else "public.news_detail"
            urls.append({"loc": url_for(endpoint, slug=row["slug"], _external=True), "lastmod": row["lastmod"] or today, "changefreq": "monthly", "priority": "0.6"})
    xml = render_template("public/sitemap.xml", urls=urls)
    resp = make_response(xml, 200)
    resp.headers["Content-Type"] = "application/xml; charset=utf-8"
    return resp
