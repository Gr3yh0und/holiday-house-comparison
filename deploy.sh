#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/deploy.config"
LOCAL_FILE="$SCRIPT_DIR/public/index.html"

if [ ! -f "$CONFIG_FILE" ]; then
  echo "Error: deploy.config not found."
  echo "Copy deploy.config.template to deploy.config and fill in your credentials."
  exit 1
fi

# shellcheck source=deploy.config.template
source "$CONFIG_FILE"

if [ ! -f "$LOCAL_FILE" ]; then
  echo "Error: public/index.html not found. Run 'python app.py' first."
  exit 1
fi

echo "Deploying public/index.html to ftp://$FTP_HOST$FTP_REMOTE_PATH/ ..."

curl --silent --show-error \
  --ftp-create-dirs \
  -T "$LOCAL_FILE" \
  "ftp://$FTP_HOST$FTP_REMOTE_PATH/index.html" \
  --user "$FTP_USER:$FTP_PASS"

echo "Done."
