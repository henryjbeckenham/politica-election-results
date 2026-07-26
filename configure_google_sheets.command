#!/bin/sh
set -eu

cd "$(dirname "$0")"
CREDENTIAL="$HOME/Desktop/Business/Politica/Politica Credentials/politica-grand-database-reader.json"

if [ ! -f "$CREDENTIAL" ]; then
  echo "Politica could not find the Google service-account file at:"
  echo "$CREDENTIAL"
  echo
  echo "No setting was changed. Put the JSON file at that location, then run this file again."
  exit 1
fi

umask 077
printf '%s\n' \
  "POLITICA_GOOGLE_SERVICE_ACCOUNT_FILE=\"$CREDENTIAL\"" \
  "POLITICA_GRAND_DATABASE_ID=1dEqeqQU3fbbow8JBoQypEFDJL7K7bek1mG_7PmVL0aI" \
  > .env
chmod 600 .env

echo "Google Sheets read-only access is configured for this Politica folder."
echo "You may now launch start_politica.command without the long Terminal command."
