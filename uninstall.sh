#!/usr/bin/env bash
#
# Uninstall PC Remote. Stops and removes the services and system files.
# By default it KEEPS your config and password (~/.config/pc-remote).
# To remove those too:  ./uninstall.sh --purge

set -euo pipefail
c_ok()   { printf '\033[32m✓\033[0m %s\n' "$*"; }
c_info() { printf '\033[36m•\033[0m %s\n' "$*"; }

[[ "${EUID}" -ne 0 ]] || { echo "Run as a normal user, not root." >&2; exit 1; }

PURGE=0
[[ "${1:-}" == "--purge" ]] && PURGE=1

for svc in pc-remote.service hid-gadget.service; do
    sudo systemctl disable --now "$svc" >/dev/null 2>&1 || true
    sudo rm -f "/etc/systemd/system/$svc"
    c_ok "Removed service $svc"
done
sudo systemctl daemon-reload

sudo rm -f /usr/local/bin/hid-gadget.sh
c_ok "Removed /usr/local/bin/hid-gadget.sh"

rm -rf "$HOME/pc-remote"
c_ok "Removed $HOME/pc-remote"

if [[ "$PURGE" -eq 1 ]]; then
    rm -rf "$HOME/.config/pc-remote"
    c_ok "Removed config and password (~/.config/pc-remote)"
else
    c_info "Kept config and password in ~/.config/pc-remote (use --purge to delete)."
fi

c_info "Left dtoverlay=dwc2 in config.txt untouched (remove it manually if you want)."
