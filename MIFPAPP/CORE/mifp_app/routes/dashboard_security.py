from __future__ import annotations

from flask import current_app, render_template, request, session

from ..services.security_status import (
    application_security_status,
    current_session_status,
    recent_security_events,
)
from ..utils.security import get_client_ip
from .auth import login_required
from .dashboard import bp


@bp.get("/security")
@login_required
def security():
    posture = application_security_status(current_app.config)
    events = recent_security_events(current_app.config["LOG_DIR"])
    active_session = current_session_status(
        session,
        client_ip=get_client_ip(),
        user_agent=request.user_agent.string,
    )
    return render_template(
        "dashboard/security.html",
        posture=posture,
        events=events,
        active_session=active_session,
    )
