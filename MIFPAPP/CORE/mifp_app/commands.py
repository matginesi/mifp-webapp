from __future__ import annotations

from pathlib import Path

import click
from flask import current_app
from flask.cli import with_appcontext

from .services.event_republication import republish_all_event_sites
from .services.event_site_publisher import PublicationError, publisher_from_config


@click.command("events-republish-all")
@with_appcontext
def events_republish_all() -> None:
    """Rebuild published event sites from retained validated WEBSITE ZIPs."""
    try:
        results = republish_all_event_sites(
            database_path=Path(current_app.config["DATABASE_PATH"]),
            conferences_root=Path(current_app.config["CONFERENCES_DIR"]),
            temporary_root=Path(current_app.config["TMP_DIR"]),
            publisher=publisher_from_config(current_app.config),
        )
    except PublicationError as exc:
        raise click.ClickException(str(exc)) from None

    succeeded = sum(result.success for result in results)
    failed = len(results) - succeeded
    for result in results:
        state = "OK" if result.success else "FAILED"
        click.echo(f"{state} {result.public_path}: {result.message}")
    click.echo(
        f"Event-site republication summary: {succeeded} succeeded, {failed} failed, "
        f"{len(results)} total."
    )
    if failed:
        raise click.exceptions.Exit(1)
