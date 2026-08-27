#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./deploy.sh                 # build + deploy public/index.html as index.php
#   ./deploy.sh --rollback      # re-upload the previous release's page verbatim
#   ./deploy.sh --rollback 2    # go back 2 releases instead of 1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOMELAB_ENV="/etc/homelab/holiday-house-comparison.env"
LEGACY_CONFIG="$SCRIPT_DIR/deploy.config"
LOCAL_FILE="$SCRIPT_DIR/public/index.html"
AUTH_DIR="$SCRIPT_DIR/deploy_auth"
AUTH_STATE_FILE="$AUTH_DIR/.auth_state"
AUTH_SECRET_PHP="$AUTH_DIR/auth_secret.php"
AUTH_COOKIE_SECONDS=$((30 * 24 * 60 * 60))

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

for key in FTP_HOST FTP_USER FTP_PASS FTP_REMOTE_PATH SITE_PASSWORD; do
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

if [ "${1:-}" = "--rollback" ]; then
  STEPS_BACK="${2:-1}"
  readarray -t RELEASES < <(list_releases)
  if [ "${#RELEASES[@]}" -le "$STEPS_BACK" ]; then
    echo "Error: only ${#RELEASES[@]} release(s) saved locally under $RELEASES_DIR -- cannot go back $STEPS_BACK."
    exit 1
  fi
  TARGET="${RELEASES[$STEPS_BACK]}"
  PAGE="$TARGET/index.php"
  if [ ! -f "$PAGE" ]; then
    echo "Error: $TARGET has no saved page -- nothing to roll back to."
    exit 1
  fi
  echo "Rolling back to release $(basename "$TARGET") ..."
  upload "$PAGE" "index.php"
  echo "Done. Live site now serving release $(basename "$TARGET")."
  echo "Note: only the page is restored -- auth gate files are untouched (they don't change per-release)."
  exit 0
fi

if [ ! -f "$LOCAL_FILE" ]; then
  echo "Error: public/index.html not found. Run 'python app.py' first."
  exit 1
fi

# ── Auth gate ────────────────────────────────────────────────────────────────
# The plaintext SITE_PASSWORD never leaves this process -- only a salted SHA-256
# hash is written to auth_secret.php (generated, gitignored), checked with
# hash_equals() by deploy_auth/login.php for a timing-safe comparison. Salt and
# cookie secret persist across deploys in .auth_state (generated, gitignored) so
# re-deploying doesn't invalidate every saved login; only a real SITE_PASSWORD
# change does that (deliberately -- see deploy_auth/_auth_gate.php).
mkdir -p "$AUTH_DIR"
if [ -f "$AUTH_STATE_FILE" ]; then
  # shellcheck source=/dev/null
  source "$AUTH_STATE_FILE"
else
  AUTH_SALT="$(openssl rand -hex 16)"
  AUTH_COOKIE_SECRET="$(openssl rand -hex 32)"
  printf 'AUTH_SALT=%s\nAUTH_COOKIE_SECRET=%s\n' "$AUTH_SALT" "$AUTH_COOKIE_SECRET" > "$AUTH_STATE_FILE"
fi

# macOS ships `shasum`, not GNU coreutils' `sha256sum` -- try the Linux name first.
if command -v sha256sum >/dev/null 2>&1; then
  PW_HASH="$(printf '%s%s' "$AUTH_SALT" "$SITE_PASSWORD" | sha256sum | cut -d' ' -f1)"
else
  PW_HASH="$(printf '%s%s' "$AUTH_SALT" "$SITE_PASSWORD" | shasum -a 256 | cut -d' ' -f1)"
fi

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

echo "Deploying to ftp://$FTP_HOST$FTP_REMOTE_PATH/ ..."

upload "$GATED_PAGE" "index.php"
upload "$AUTH_DIR/_auth_gate.php" "_auth_gate.php"
upload "$AUTH_SECRET_PHP" "auth_secret.php"
upload "$AUTH_DIR/login.php" "login.php"
upload "$AUTH_DIR/robots.txt" "robots.txt"

# Snapshot the exact gated bytes just uploaded -- not public/index.html, which
# has no auth gate and isn't what's actually live.
snapshot_release "$GATED_PAGE" "index.php"

echo "Done."
