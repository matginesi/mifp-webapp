"""Retired personal-invitation URLs: never accept new legacy responses."""
from flask import make_response

from .public import bp


@bp.get("/respond")
@bp.get("/respond/poll")
@bp.post("/respond/exchange")
@bp.post("/respond/submit")
def respond():
    response = make_response("This personal invitation is retired. Ask the organiser for the current shared survey link.", 410)
    response.headers.update({"Cache-Control": "no-store, max-age=0", "Referrer-Policy": "no-referrer",
                             "X-Robots-Tag": "noindex, nofollow"})
    return response
