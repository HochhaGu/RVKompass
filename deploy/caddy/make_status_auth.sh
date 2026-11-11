#!/usr/bin/env bash
# Erzeugt den Passwortschutz-Baustein für die Statusseite aus STATUS_USER und STATUS_PASSWORD
# in .env. Das Passwort wird nie angezeigt: es geht nur per Pipe an "caddy hash-password".
# Ausgabe (auf stdout) ist z. B.:
#   basicauth {
#   	guenther $2a$14$...
#   }
# Verwendung:
#   bash deploy/caddy/make_status_auth.sh | sudo tee /etc/caddy/rvkompass-status.auth >/dev/null
set -euo pipefail

repo="$(cd "$(dirname "$0")/../.." && pwd)"
py="$repo/.venv/bin/python"
envfile="$repo/.env"
[ -f "$envfile" ] || { echo "Fehler: $envfile fehlt" >&2; exit 1; }

read_key() {
	"$py" -c 'import sys
from dotenv import dotenv_values
print(dotenv_values(sys.argv[1]).get(sys.argv[2]) or "")' "$envfile" "$1"
}

user="$(read_key STATUS_USER)"
if [ -z "$user" ] || [[ "$user" =~ [[:space:]] ]]; then
	echo "Fehler: STATUS_USER fehlt in .env oder enthält Leerzeichen" >&2
	exit 1
fi
if [ -z "$(read_key STATUS_PASSWORD)" ]; then
	echo "Fehler: STATUS_PASSWORD fehlt in .env" >&2
	exit 1
fi

hash="$(read_key STATUS_PASSWORD | caddy hash-password)"
printf 'basicauth {\n\t%s %s\n}\n' "$user" "$hash"
