#!/usr/bin/env bash
set -euo pipefail

# Toggle public web exposure without stopping backend services.
# - close: block inbound web access (HTTPS/HTTP + direct app ports)
# - open:  allow inbound HTTPS on 443 for reverse-proxy access
# - status: show current web exposure in active firewalld zone

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root (e.g. sudo scripts/toggle-public-web.sh <open|close|status>)" >&2
  exit 1
fi

ACTION="${1:-status}"
ZONE="${2:-public}"

show_status() {
  echo "Zone: ${ZONE}"
  firewall-cmd --zone="${ZONE}" --list-services
  firewall-cmd --zone="${ZONE}" --list-ports
  firewall-cmd --zone="${ZONE}" --list-forward-ports
}

close_public_web() {
  # Keep SSH available; remove public web surfaces.
  firewall-cmd --zone="${ZONE}" --remove-service=https --permanent || true
  firewall-cmd --zone="${ZONE}" --remove-service=http --permanent || true

  firewall-cmd --zone="${ZONE}" --remove-port=443/tcp --permanent || true
  firewall-cmd --zone="${ZONE}" --remove-port=4173/tcp --permanent || true
  firewall-cmd --zone="${ZONE}" --remove-port=8000/tcp --permanent || true
  firewall-cmd --zone="${ZONE}" --remove-port=8080/tcp --permanent || true

  firewall-cmd --zone="${ZONE}" --remove-forward-port=port=8000:proto=tcp:toport=8080 --permanent || true

  firewall-cmd --reload
  echo "Public web access closed."
  show_status
}

open_public_web() {
  # Recommended public surface: HTTPS reverse proxy on 443.
  firewall-cmd --zone="${ZONE}" --add-service=https --permanent
  firewall-cmd --reload
  echo "Public web access opened (HTTPS/443)."
  show_status
}

case "${ACTION}" in
  open)
    open_public_web
    ;;
  close)
    close_public_web
    ;;
  status)
    show_status
    ;;
  *)
    echo "Usage: $0 <open|close|status> [zone]" >&2
    exit 2
    ;;
esac
