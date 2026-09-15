#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="$ROOT_DIR"
PREFIX="mifp-codebase"
DRY_RUN=0

usage() {
  cat <<'USAGE'
Crea uno ZIP sicuro del codebase per analisi e coding con ChatGPT.

Include:
  - tutti i file tracciati da Git, comprese le modifiche non committate;
  - i nuovi file non ignorati da Git;
  - codice, test, documentazione, configurazioni pubbliche e file di deploy.

Esclude sempre:
  - .git, configurazioni locali, file degli agenti, virtualenv e cache;
  - output degli scraper, database, asset runtime, backup, log ed export;
  - .env reali, credenziali, chiavi, token, dump e archivi;
  - file nuovi non tracciati oltre 5 MiB (gli asset sorgente tracciati restano inclusi).

Lo script rifiuta inoltre di creare lo ZIP se trova nel materiale selezionato
impronte ad alta confidenza di chiavi private o token reali.

Uso:
  ./zip_it.sh
  ./zip_it.sh --output /percorso/destinazione
  ./zip_it.sh --prefix mifp-snapshot
  ./zip_it.sh --dry-run
USAGE
}

while (($#)); do
  case "$1" in
    --output)
      [[ $# -ge 2 && -n "$2" ]] || { echo "ERROR: --output richiede una directory" >&2; exit 2; }
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --output=*) OUTPUT_DIR="${1#*=}"; shift ;;
    --prefix)
      [[ $# -ge 2 && -n "$2" ]] || { echo "ERROR: --prefix richiede un valore" >&2; exit 2; }
      PREFIX="$2"
      shift 2
      ;;
    --prefix=*) PREFIX="${1#*=}"; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: opzione sconosciuta: $1" >&2; exit 2 ;;
  esac
done

[[ "$PREFIX" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || {
  echo "ERROR: prefix non valido: $PREFIX" >&2
  exit 2
}

mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"

last=0
while IFS= read -r -d '' existing; do
  filename="${existing##*/}"
  number="${filename#"${PREFIX}-"}"
  number="${number%.zip}"
  if [[ "$number" =~ ^[0-9]+$ ]] && ((10#$number > last)); then
    last=$((10#$number))
  fi
done < <(find "$OUTPUT_DIR" -maxdepth 1 -type f -name "${PREFIX}-*.zip" -print0 2>/dev/null)

OUTPUT="$(printf '%s/%s-%03d.zip' "$OUTPUT_DIR" "$PREFIX" "$((last + 1))")"

if ((DRY_RUN)); then
  printf '%s\n' "$OUTPUT"
  exit 0
fi

python3 - "$ROOT_DIR" "$OUTPUT" <<'PY'
from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath

root = Path(sys.argv[1]).resolve()
output = Path(sys.argv[2]).resolve()

MAX_UNTRACKED_FILE_BYTES = 5 * 1024 * 1024

EXCLUDED_DIR_NAMES = {
    ".git", ".mifp", ".agents", ".codex", ".superpowers",
    ".venv", ".venv-production", "venv", "env", "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".hypothesis", ".tox", ".idea", ".vscode", ".cache", "htmlcov",
    "build", "dist", "instance", "playwright-report", "test-results",
    "tmp", "temp",
}

EXCLUDED_TOP_LEVEL = {
    "IMPORT_DATA", "NEW_IMPORT_DATA", "SCRAPER", "scrapers_",
}

EXCLUDED_PATH_PREFIXES = {
    "MIFPAPP/DATABASE/assets",
    "MIFPAPP/DATABASE/backups",
    "MIFPAPP/DATABASE/config",
    "MIFPAPP/DATABASE/conferences",
    "MIFPAPP/DATABASE/exports",
    "MIFPAPP/DATABASE/logs",
    "MIFPAPP/DATABASE/tmp",
    "MIFPAPP/DATABASE/uploads",
    "MIFPAPP/CORE/secrets",
    "SCRAPERS/OUTPUTS",
}

EXCLUDED_FILE_PATTERNS = {
    "*.pyc", "*.pyo", "*.log", "*.pid", "*.zip", "*.tar", "*.tar.gz",
    "*.tgz", "*.bak", "*.dump", "*.db", "*.db-*", "*.sqlite",
    "*.sqlite-*", "*.sqlite3", "*.sqlite3-*", "*.jsonl", "*.ndjson",
    "*.coverage", "coverage.xml", ".DS_Store", "Thumbs.db", "*.orig",
    "*.rej", "*.swp", "*.swo", "*~", "credentials.json",
    "credentials.*.json", "*.secret", "*.token", "*.key", "*.pem",
    "*.p12", "*.pfx", "*.keystore", ".htpasswd", "id_rsa", "id_dsa",
    "id_ecdsa", "id_ed25519",
}

ALLOWED_ENV_TEMPLATES = {
    "MIFPAPP/CORE/.env.example",
    "deploy/.env.production.example",
}

# Limitato a impronte forti per non bloccare esempi come SECRET_KEY='CHANGE-ME'.
SECRET_FINGERPRINTS = {
    "private key": re.compile(
        rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
    ),
    "AWS access key": re.compile(rb"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])"),
    "GitHub token": re.compile(rb"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9_]{30,}"),
    "OpenAI API key": re.compile(rb"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{20,}"),
    "Slack token": re.compile(rb"(?<![A-Za-z0-9-])xox[baprs]-[A-Za-z0-9-]{10,}"),
    "Stripe live key": re.compile(rb"(?<![A-Za-z0-9_])sk_live_[A-Za-z0-9]{16,}"),
}


def exclusion_reason(rel: PurePosixPath) -> str | None:
    text = rel.as_posix()
    if not rel.parts:
        return None
    if rel.parts[0] in EXCLUDED_TOP_LEVEL:
        return "runtime/generated path"
    if any(part in EXCLUDED_DIR_NAMES for part in rel.parts[:-1]):
        return "cache/local directory"
    if any(text == prefix or text.startswith(prefix + "/") for prefix in EXCLUDED_PATH_PREFIXES):
        return "runtime/generated path"
    if rel.name == ".env" or rel.name.startswith(".env."):
        return None if text in ALLOWED_ENV_TEMPLATES else "local environment file"
    lowered = rel.name.lower()
    if any(fnmatch.fnmatch(lowered, pattern.lower()) for pattern in EXCLUDED_FILE_PATTERNS):
        return "data, secret-like or generated file"
    return None


def git_candidates() -> tuple[list[PurePosixPath], bool]:
    probe = subprocess.run(
        ["git", "-C", os.fspath(root), "rev-parse", "--show-toplevel"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if probe.returncode != 0:
        return [], False
    git_root_text = probe.stdout.decode("utf-8", errors="surrogateescape").strip()
    try:
        git_root = Path(git_root_text).resolve()
    except (OSError, RuntimeError):
        return [], False
    # A copied/extracted snapshot may live below some unrelated Git checkout.
    # Its parent repository index must never be applied to the snapshot root.
    if git_root != root:
        return [], False
    result = subprocess.run(
        [
            "git", "-C", os.fspath(root), "ls-files", "-z", "--cached",
            "--others", "--exclude-standard",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise SystemExit(f"Impossibile leggere i file Git: {detail}")
    return (
        [
            PurePosixPath(item.decode("utf-8", errors="surrogateescape"))
            for item in result.stdout.split(b"\0")
            if item
        ],
        True,
    )


def fallback_candidates() -> list[PurePosixPath]:
    """Supporta uno snapshot già estratto, nel quale .git non è disponibile."""
    found: list[PurePosixPath] = []
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        rel_current = PurePosixPath(current_path.relative_to(root).as_posix())
        kept_dirs: list[str] = []
        for dirname in sorted(dirnames):
            rel = rel_current / dirname if rel_current.as_posix() != "." else PurePosixPath(dirname)
            if exclusion_reason(rel / "placeholder") is None:
                kept_dirs.append(dirname)
        dirnames[:] = kept_dirs
        for filename in sorted(filenames):
            path = current_path / filename
            if path.resolve() == output:
                continue
            found.append(PurePosixPath(path.relative_to(root).as_posix()))
    return found


candidates, used_git = git_candidates()
if not used_git:
    candidates = fallback_candidates()

tracked: set[PurePosixPath] = set()
if used_git:
    result = subprocess.run(
        ["git", "-C", os.fspath(root), "ls-files", "-z", "--cached"],
        stdout=subprocess.PIPE,
        check=True,
    )
    tracked = {
        PurePosixPath(item.decode("utf-8", errors="surrogateescape"))
        for item in result.stdout.split(b"\0")
        if item
    }

files: list[tuple[Path, str]] = []
excluded: Counter[str] = Counter()
secret_hits: list[tuple[str, str]] = []

for rel in sorted(set(candidates), key=lambda item: item.as_posix()):
    reason = exclusion_reason(rel)
    if reason is not None:
        excluded[reason] += 1
        continue

    path = root / rel.as_posix()
    if path.is_symlink():
        excluded["symbolic link"] += 1
        continue
    if not path.is_file():
        continue
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise SystemExit(f"Impossibile leggere {rel.as_posix()}: {exc}") from exc

    # Gli asset sotto controllo versione sono intenzionali. I file locali
    # grandi e inattesi non appartengono a uno snapshot compatto del codice.
    if rel not in tracked and size > MAX_UNTRACKED_FILE_BYTES:
        excluded["untracked file over 5 MiB"] += 1
        continue

    try:
        content = path.read_bytes()
    except OSError as exc:
        raise SystemExit(f"Impossibile leggere {rel.as_posix()}: {exc}") from exc
    for label, pattern in SECRET_FINGERPRINTS.items():
        if pattern.search(content):
            secret_hits.append((rel.as_posix(), label))

    files.append((path, rel.as_posix()))

if secret_hits:
    print("ZIP NON creato: possibili credenziali reali nei file selezionati:", file=sys.stderr)
    for filename, label in sorted(secret_hits):
        print(f"  - {filename}: {label}", file=sys.stderr)
    print("Rimuovi o sostituisci i segreti e riprova.", file=sys.stderr)
    raise SystemExit(1)

if not files:
    raise SystemExit("Nessun file sorgente sicuro da archiviare")

output.parent.mkdir(parents=True, exist_ok=True)
temp_name: str | None = None
try:
    with tempfile.NamedTemporaryFile(
        prefix=f".{output.stem}-", suffix=".tmp", dir=output.parent, delete=False
    ) as temp:
        temp_name = temp.name
    with zipfile.ZipFile(
        temp_name, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path, arcname in files:
            archive.write(path, arcname)

    # Lo ZIP diventa visibile col nome finale soltanto dopo la verifica.
    with zipfile.ZipFile(temp_name, "r") as archive:
        names = archive.namelist()
        if len(names) != len(files) or archive.testzip() is not None:
            raise RuntimeError("verifica di integrità ZIP fallita")
        if any(PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts for name in names):
            raise RuntimeError("percorso ZIP non sicuro")
    os.replace(temp_name, output)
    temp_name = None
except Exception:
    if temp_name is not None:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    raise

mode = "Git (tracciati + nuovi non ignorati)" if used_git else "scansione snapshot"
print(f"Selezione: {mode}")
print(f"File inclusi: {len(files)}")
if excluded:
    print("File esclusi:")
    for reason, count in sorted(excluded.items()):
        print(f"  - {count}: {reason}")
PY

printf 'Creato: %s (%s)\n' "$OUTPUT" "$(du -h "$OUTPUT" | cut -f1)"
printf 'Verificato: nessun DB, output scraper, file runtime, cache o secret noto incluso.\n'
