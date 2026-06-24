#!/bin/bash
# Optional: grant llamawatch permission to restart a service without a password.
# Used by quick-action buttons that run `sudo systemctl ...`.
#
# Usage:  sudo ./setup-sudoers.sh <service-name>
# Example: sudo ./setup-sudoers.sh my-llm-proxy
USER_NAME="${SUDO_USER:-$(whoami)}"
SERVICE="${1:?Usage: sudo ./setup-sudoers.sh <service-name>}"

# Reject anything but a simple service name (no spaces/metacharacters) so the
# generated sudoers rule can't be widened. Re-running overwrites the file.
if ! printf '%s' "$SERVICE" | grep -qE '^[A-Za-z0-9._@-]+$'; then
  echo "Invalid service name: '$SERVICE' (letters, digits, . _ @ - only)" >&2
  exit 1
fi

cat > "/etc/sudoers.d/llamawatch" << SUDOERS
$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl restart $SERVICE.service
$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl stop $SERVICE.service
$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl start $SERVICE.service
SUDOERS
chmod 440 /etc/sudoers.d/llamawatch
echo "Done — sudoers entry created for $USER_NAME on $SERVICE.service"
