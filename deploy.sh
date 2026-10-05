#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./deploy.sh                        # build + deploy public/index.html as index.php (FTP)
#   ./deploy.sh --target local         # build + copy the plain page to LOCAL_DEPLOY_PATH instead
#   ./deploy.sh --target both          # build + deploy to both FTP and LOCAL_DEPLOY_PATH
#   ./deploy.sh --rollback             # re-upload the previous release's page verbatim (FTP only)
#   ./deploy.sh --rollback 2           # go back 2 releases instead of 1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOMELAB_ENV="${HOMELAB_ENV:-/etc/homelab/holiday-house-comparison.env}"
LEGACY_CONFIG="$SCRIPT_DIR/deploy.config"
LOCAL_FILE="$SCRIPT_DIR/public/index.html"
AUTH_DIR="$SCRIPT_DIR/deploy_auth"
AUTH_STATE_FILE="$AUTH_DIR/.auth_state"
AUTH_SECRET_PHP="$AUTH_DIR/auth_secret.php"
AUTH_COOKIE_SECONDS=$((30 * 24 * 60 * 60))

# ── Argument parsing ─────────────────────────────────────────────────────────
TARGET="ftp"
DO_ROLLBACK=false
STEPS_BACK=1
while [ $# -gt 0 ]; do
  case "$1" in
    --rollback)
      DO_ROLLBACK=true
      if [ $# -ge 2 ]; then
        case "$2" in
          --*) : ;;
          *) STEPS_BACK="$2"; shift ;;
        esac
      fi
      ;;
    --target)
      if [ $# -lt 2 ]; then
        echo "Error: --target requires a value (ftp, local, or both)."
        exit 1
      fi
      TARGET="$2"
      shift
      ;;
    *)
      echo "Error: unknown argument '$1'."
      exit 1
      ;;
  esac
  shift
done

case "$TARGET" in
  ftp | local | both) ;;
  *)
    echo "Error: --target must be ftp, local, or both (got '$TARGET')."
    exit 1
    ;;
esac

if [ "$DO_ROLLBACK" = true ] && [ "$TARGET" != "ftp" ]; then
  echo "Error: --rollback only supports --target ftp -- releases are only snapshotted for FTP deploys."
  exit 1
fi

if ! [[ "$STEPS_BACK" =~ ^[0-9]+$ ]]; then
  echo "Error: --rollback steps must be a non-negative integer, got '$STEPS_BACK'."
  exit 1
fi

# ── Rollback ─────────────────────────────────────────────────────────────────
# Every deploy snapshots the exact gated bytes it uploads -- the .php page
# *after* the auth-gate prefix is added, not the pre-gate public/index.html --
# so a rollback can never republish a page missing its auth check. FTP has no
# atomic flip (unlike a local symlink target, §13B), so this re-uploads a
# known-good snapshot verbatim rather than rebuilding and hoping.
RELEASES_DIR="$SCRIPT_DIR/releases/prod"
KEEP_RELEASES=5

list_releases() {
  find "$RELEASES_DIR" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort -r
}

snapshot_release() {
  local content_file="$1" page_name="$2"
  mkdir -p "$RELEASES_DIR"
  local stamp
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"

  # The sort key is a monotonic sequence number, not the timestamp alone.
  # Two deploys can land in the same second (this repo's own tests do), and
  # disambiguating by "does this directory already exist" breaks once an
  # earlier same-second directory has been pruned away: its name becomes
  # free again, sorts *before* every suffixed name as a plain string
  # (a bare prefix always sorts before anything with a suffix appended), and
  # a later deploy reusing it would look older than it actually is -- and
  # get pruned as if it were the oldest release, deleting the newest one
  # instead. A sequence number that only ever increases can't be reused.
  local next_seq=1 d name existing_seq
  for d in "$RELEASES_DIR"/*/; do
    [ -d "$d" ] || continue
    name="$(basename "$d")"
    existing_seq="${name%%-*}"
    if [[ "$existing_seq" =~ ^[0-9]+$ ]] && [ "$((10#$existing_seq + 1))" -gt "$next_seq" ]; then
      next_seq=$((10#$existing_seq + 1))
    fi
  done
  local release_dir="$RELEASES_DIR/$(printf '%06d' "$next_seq")-$stamp"
  mkdir -p "$release_dir"
  cp "$content_file" "$release_dir/$page_name"

  local releases=() i
  while IFS= read -r d; do releases+=("$d"); done < <(list_releases)
  for ((i = KEEP_RELEASES; i < ${#releases[@]}; i++)); do
    rm -rf "${releases[$i]}"
  done
}

# Credentials + SITE_PASSWORD: /etc/homelab/holiday-house-comparison.env (§4) if
# present, else the legacy repo-local deploy.config -- kept only for machines that
# predate this server's onboarding (see compliance/holiday-house-comparison.md §4
# in the infrastructure repo). Prefer the env file; it's what keeps these values
# off server-backup-sync.sh's wholesale /samba/ rsync.
if [ -f "$HOMELAB_ENV" ]; then
  CONFIG_FILE="$HOMELAB_ENV"
elif [ -f "$LEGACY_CONFIG" ]; then
  echo "Warning: reading credentials from $LEGACY_CONFIG (legacy, repo-local) -- move them to $HOMELAB_ENV once this runs on the Linux server."
  CONFIG_FILE="$LEGACY_CONFIG"
else
  echo "Error: no config found."
  echo "On the server: create $HOMELAB_ENV (see .env.example)."
  echo "On the workstation: copy deploy.config.template to deploy.config and fill in your credentials."
  exit 1
fi

# shellcheck source=deploy.config.template
source "$CONFIG_FILE"

REQUIRED_KEYS=(SITE_PASSWORD)
if [ "$TARGET" = "ftp" ] || [ "$TARGET" = "both" ]; then
  REQUIRED_KEYS+=(FTP_HOST FTP_USER FTP_PASS FTP_REMOTE_PATH)
fi
if [ "$TARGET" = "local" ] || [ "$TARGET" = "both" ]; then
  REQUIRED_KEYS+=(LOCAL_DEPLOY_PATH)
fi
for key in "${REQUIRED_KEYS[@]}"; do
  if [ -z "${!key:-}" ]; then
    echo "Error: $CONFIG_FILE is missing key: $key"
    exit 1
  fi
done
if [ "$SITE_PASSWORD" = "change-me" ]; then
  echo "Error: $CONFIG_FILE's SITE_PASSWORD is still the template placeholder -- set a real passphrase."
  exit 1
fi

upload() {
  local src="$1" name="$2"
  curl --silent --show-error \
    --ftp-create-dirs \
    -T "$src" \
    "ftp://$FTP_HOST$FTP_REMOTE_PATH/$name" \
    --user "$FTP_USER:$FTP_PASS"
}

# Best-effort delete of a stale remote file -- e.g. the unprotected index.html
# left behind by every deploy from before the auth gate existed (§5). Ignore
# failures: the file may already be gone, and a missing DELE target must not
# abort an otherwise-successful deploy.
delete_remote() {
  local name="$1"
  curl --silent --show-error \
    --user "$FTP_USER:$FTP_PASS" \
    -Q "DELE $FTP_REMOTE_PATH/$name" \
    "ftp://$FTP_HOST/" >/dev/null 2>&1 || true
}

# The local target is served by the homelab proxy's Caddy file_server, which
# never executes PHP -- it streams .php files back as plain text, so copying
# the gate there would publish auth_secret.php's hash and gate nothing. Access
# control on that route is Authelia's forward_auth instead (homelab.yml
# personal_data: true), so the local target gets the plain, ungated page.
# Any PHP files an earlier version of this script left behind are removed.
deploy_local() {
  local page_name="$1"
  mkdir -p "$LOCAL_DEPLOY_PATH"
  cp "$LOCAL_FILE" "$LOCAL_DEPLOY_PATH/$page_name"
  cp "$AUTH_DIR/robots.txt" "$LOCAL_DEPLOY_PATH/robots.txt"
  rm -f "$LOCAL_DEPLOY_PATH/_auth_gate.php" "$LOCAL_DEPLOY_PATH/auth_secret.php" \
    "$LOCAL_DEPLOY_PATH/login.php" "$LOCAL_DEPLOY_PATH/${page_name%.html}.php"
}

# health.json (WEBAPP_PROJECT_STANDARD.md §6a) is deliberately unauthenticated
# -- it deploys plain, never through the PHP gate -- so a monitoring consumer
# at <route>/health doesn't need a saved login.
publish_health_best_effort() {
  local health_file="$SCRIPT_DIR/public/health.json"
  [ -f "$health_file" ] || return 0
  if [ "$TARGET" = "ftp" ] || [ "$TARGET" = "both" ]; then
    upload "$health_file" "health.json" || true
  fi
  if [ "$TARGET" = "local" ] || [ "$TARGET" = "both" ]; then
    mkdir -p "$LOCAL_DEPLOY_PATH"
    cp "$health_file" "$LOCAL_DEPLOY_PATH/health.json" || true
  fi
}

if [ "$DO_ROLLBACK" = true ]; then
  readarray -t RELEASES < <(list_releases)
  if [ "${#RELEASES[@]}" -le "$STEPS_BACK" ]; then
    echo "Error: only ${#RELEASES[@]} release(s) saved locally under $RELEASES_DIR -- cannot go back $STEPS_BACK."
    exit 1
  fi
  ROLLBACK_TARGET="${RELEASES[$STEPS_BACK]}"
  PAGE="$ROLLBACK_TARGET/index.php"
  if [ ! -f "$PAGE" ]; then
    echo "Error: $ROLLBACK_TARGET has no saved page -- nothing to roll back to."
    exit 1
  fi
  echo "Rolling back to release $(basename "$ROLLBACK_TARGET") ..."
  upload "$PAGE" "index.php"
  echo "Done. Live site now serving release $(basename "$ROLLBACK_TARGET")."
  echo "Note: only the page is restored -- auth gate files are untouched (they don't change per-release)."
  exit 0
fi

# ── Build ────────────────────────────────────────────────────────────────────
# The deploy always builds fresh -- a stale public/index.html from an earlier,
# unrelated run must never get (re-)published silently.
PYTHON_BIN="python3"
command -v python3 >/dev/null 2>&1 || PYTHON_BIN="python"
echo "Building site (running $PYTHON_BIN app.py) ..."
if ! (cd "$SCRIPT_DIR" && "$PYTHON_BIN" app.py); then
  echo "Error: site build failed -- aborting deploy."
  # app.py still writes public/health.json with status=down on this failure
  # (WEBAPP_PROJECT_STANDARD.md §6a) -- publish just that so a monitoring
  # consumer sees the failure instead of a stale "ok" from the last good run.
  publish_health_best_effort
  exit 1
fi

if [ ! -f "$LOCAL_FILE" ]; then
  echo "Error: public/index.html not found after build."
  exit 1
fi

# ── Auth gate ────────────────────────────────────────────────────────────────
# The plaintext SITE_PASSWORD never leaves this process -- only a salted SHA-256
# hash is written to auth_secret.php (generated, gitignored), checked with
# hash_equals() by deploy_auth/login.php for a timing-safe comparison. Salt
# persists across deploys in .auth_state (generated, gitignored) so re-deploying
# doesn't invalidate every saved login. AUTH_COOKIE_SECRET also persists --
# except when SITE_PASSWORD itself changed, in which case it's rotated so every
# cookie signed with the old secret stops validating (see
# deploy_auth/_auth_gate.php): the hash alone changing isn't enough, since
# hhc_is_authed() never re-checks it against a live cookie.

# macOS ships `shasum`, not GNU coreutils' `sha256sum` -- try the Linux name first.
sha256_hex() {
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s' "$1" | sha256sum | cut -d' ' -f1
  else
    printf '%s' "$1" | shasum -a 256 | cut -d' ' -f1
  fi
}

mkdir -p "$AUTH_DIR"
PW_FINGERPRINT="$(sha256_hex "$SITE_PASSWORD")"
if [ -f "$AUTH_STATE_FILE" ]; then
  # shellcheck source=/dev/null
  source "$AUTH_STATE_FILE"
else
  AUTH_SALT="$(openssl rand -hex 16)"
  AUTH_COOKIE_SECRET="$(openssl rand -hex 32)"
  STORED_PW_FINGERPRINT=""
fi
if [ "${STORED_PW_FINGERPRINT:-}" != "$PW_FINGERPRINT" ]; then
  if [ -n "${STORED_PW_FINGERPRINT:-}" ]; then
    echo "SITE_PASSWORD changed -- rotating the auth cookie secret to invalidate existing logins."
  fi
  AUTH_COOKIE_SECRET="$(openssl rand -hex 32)"
fi
printf 'AUTH_SALT=%s\nAUTH_COOKIE_SECRET=%s\nSTORED_PW_FINGERPRINT=%s\n' \
  "$AUTH_SALT" "$AUTH_COOKIE_SECRET" "$PW_FINGERPRINT" > "$AUTH_STATE_FILE"

PW_HASH="$(sha256_hex "$AUTH_SALT$SITE_PASSWORD")"

cat > "$AUTH_SECRET_PHP" <<EOF
<?php
// Generated by deploy.sh from $CONFIG_FILE's SITE_PASSWORD -- do not edit,
// do not commit. Re-run deploy.sh after changing SITE_PASSWORD to update it.
define('SITE_PASSWORD_SALT', '$AUTH_SALT');
define('SITE_PASSWORD_HASH', '$PW_HASH');
define('AUTH_COOKIE_NAME', 'hhc_auth');
define('AUTH_COOKIE_SECRET', '$AUTH_COOKIE_SECRET');
define('AUTH_COOKIE_SECONDS', $AUTH_COOKIE_SECONDS);
EOF

# Pages deploy as .php, never .html -- specifically so the gate above runs
# before any content is sent. Prepending it to an .html file that a plain
# webserver just streams back would be decorative, not enforced.
GATED_PAGE="$(mktemp)"
trap 'rm -f "$GATED_PAGE"' EXIT
{ printf "<?php require __DIR__ . '/_auth_gate.php'; ?>\n"; cat "$LOCAL_FILE"; } > "$GATED_PAGE"

if [ "$TARGET" = "ftp" ] || [ "$TARGET" = "both" ]; then
  echo "Deploying to ftp://$FTP_HOST$FTP_REMOTE_PATH/ ..."

  upload "$GATED_PAGE" "index.php"
  upload "$AUTH_DIR/_auth_gate.php" "_auth_gate.php"
  upload "$AUTH_SECRET_PHP" "auth_secret.php"
  upload "$AUTH_DIR/login.php" "login.php"
  upload "$AUTH_DIR/robots.txt" "robots.txt"
  upload "$SCRIPT_DIR/public/health.json" "health.json"

  # Remove the unprotected index.html every pre-gate deploy left on the server
  # -- otherwise it keeps serving the full site with no login, and on a
  # typical Apache DirectoryIndex order it even wins over index.php for the
  # bare domain root, making the gate above a no-op.
  delete_remote "index.html"

  # Snapshot the exact gated bytes just uploaded -- not public/index.html,
  # which has no auth gate and isn't what's actually live.
  snapshot_release "$GATED_PAGE" "index.php"
fi

if [ "$TARGET" = "local" ] || [ "$TARGET" = "both" ]; then
  echo "Deploying to local path $LOCAL_DEPLOY_PATH ..."
  deploy_local "index.html"
  cp "$SCRIPT_DIR/public/health.json" "$LOCAL_DEPLOY_PATH/health.json"
fi

echo "Done."
