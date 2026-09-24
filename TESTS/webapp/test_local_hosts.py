from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "deploy" / "local-hosts.sh"


def _sync(hosts: Path, domain: str) -> None:
    subprocess.run(
        ["bash", str(HELPER), domain, str(hosts)],
        text=True,
        capture_output=True,
        check=True,
    )


def test_home_arpa_self_resolution_is_idempotent(tmp_path: Path) -> None:
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n", encoding="utf-8")

    _sync(hosts, "vpsbox.home.arpa")
    first = hosts.read_bytes()
    _sync(hosts, "vpsbox.home.arpa")

    text = hosts.read_text(encoding="utf-8")
    assert hosts.read_bytes() == first
    assert text.count("# BEGIN MIFP LOCAL HOSTS") == 1
    assert text.count("# END MIFP LOCAL HOSTS") == 1
    assert text.count("127.0.0.1 vpsbox.home.arpa www.vpsbox.home.arpa") == 1
    assert "events.vpsbox.home.arpa" not in text



def test_home_arpa_honors_explicit_www_and_events_domains(tmp_path: Path) -> None:
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n", encoding="utf-8")

    subprocess.run(
        [
            "bash", str(HELPER), "vpsbox.home.arpa", str(hosts),
            "www-alt.vpsbox.home.arpa", "conference.vpsbox.home.arpa",
        ],
        text=True, capture_output=True, check=True,
    )

    text = hosts.read_text(encoding="utf-8")
    assert (
        "127.0.0.1 vpsbox.home.arpa www-alt.vpsbox.home.arpa "
        "conference.vpsbox.home.arpa"
    ) in text

def test_public_domain_never_gets_local_hosts_mapping(tmp_path: Path) -> None:
    hosts = tmp_path / "hosts"
    hosts.write_text(
        "127.0.0.1 localhost\n"
        "# BEGIN MIFP LOCAL HOSTS\n"
        "127.0.0.1 old.home.arpa www.old.home.arpa events.old.home.arpa\n"
        "# END MIFP LOCAL HOSTS\n",
        encoding="utf-8",
    )

    _sync(hosts, "mifp.eu")

    text = hosts.read_text(encoding="utf-8")
    assert text.strip() == "127.0.0.1 localhost"
    assert "mifp.eu" not in text
    assert "MIFP LOCAL HOSTS" not in text


def test_local_tls_is_strictly_conditional_in_bootstrap() -> None:
    bootstrap = (ROOT / "deploy" / "bootstrap-vps.sh").read_text(encoding="utf-8")
    caddyfile = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")
    assert 'if [[ "$DOMAIN" == *.home.arpa ]]' in bootstrap
    assert 'MIFP_TLS_DIRECTIVE="tls internal"' in bootstrap
    assert 'MIFP_TLS_DIRECTIVE=""' in bootstrap
    assert "__MIFP_TLS__" in caddyfile
    assert "tls internal" not in caddyfile

    local_rendered = caddyfile.replace("__MIFP_DOMAIN__", "vpsbox.home.arpa").replace(
        "__MIFP_TLS__", "tls internal"
    )
    public_rendered = caddyfile.replace("__MIFP_DOMAIN__", "mifp.eu").replace(
        "__MIFP_TLS__", ""
    )
    assert local_rendered.count("tls internal") == 1
    assert "vpsbox.home.arpa" in local_rendered
    assert "tls internal" not in public_rendered
    assert "mifp.eu" in public_rendered
