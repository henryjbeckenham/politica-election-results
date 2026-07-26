#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PAYLOAD="$SCRIPT_DIR/payload"
TARGET=${1:-"$HOME/Downloads/Politica_Election_Results_Database"}
BACKUP=""

on_exit() {
  status=$?
  if [ "$status" -ne 0 ]; then
    echo
    echo "Stage 10.1 stopped before every verification passed."
    echo "The prior immutable release was not deleted or modified."
    echo "No raw source, Google Sheets credential or Grand Database row was changed."
    if [ -n "$BACKUP" ]; then
      echo "The pre-update application-code and active-pointer backup is at:"
      echo "$BACKUP"
    fi
  fi
}
trap on_exit EXIT

if [ ! -d "$PAYLOAD/src/politica_erd" ] || [ ! -f "$PAYLOAD/pyproject.toml" ]; then
  echo "The Stage 10.1 payload is missing. Extract the complete ZIP before running this file."
  exit 1
fi

if [ ! -d "$TARGET" ]; then
  echo "The installed Politica folder was not found at:"
  echo "$TARGET"
  echo "No change was made."
  exit 1
fi

TARGET=$(CDPATH= cd -- "$TARGET" && pwd)
case "$TARGET" in
  /|"$HOME")
    echo "The target path is too broad; installation was refused."
    exit 1
    ;;
esac

for required in pyproject.toml src/politica_erd/app/service.py data/app/releases/active.json; do
  if [ ! -e "$TARGET/$required" ]; then
    echo "This does not appear to be the tested Politica installation:"
    echo "$TARGET"
    echo "Missing: $required"
    exit 1
  fi
done

if ! grep -Eq '^version = "1\.0\.(0|1)"$' "$TARGET/pyproject.toml"; then
  echo "Stage 10.1 expects the installed application to be version 1.0.0 or 1.0.1."
  echo "No change was made."
  exit 1
fi

if command -v lsof >/dev/null 2>&1 && lsof -tiTCP:8765 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Politica is still running on port 8765."
  echo "Return to its Terminal window, press Control-C once, then run this installer again."
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "The uv command is unavailable. The existing Politica installation cannot be verified."
  exit 1
fi

for filename in \
  HouseCandidatesDownload-31496.csv \
  SenateCandidatesDownload-31496.csv \
  HouseFirstPrefsByCandidateByVoteTypeDownload-31496.csv \
  HouseTcpByCandidateByVoteTypeDownload-31496.csv \
  HouseTppByDivisionDownload-31496.csv \
  HouseMembersElectedDownload-31496.csv \
  GeneralEnrolmentByDivisionDownload-31496.csv \
  HouseInformalByDivisionDownload-31496.csv \
  HouseTurnoutByDivisionDownload-31496.csv \
  HouseVotesCountedByDivisionDownload-31496.csv \
  SenateFirstPrefsByStateByVoteTypeDownload-31496.csv \
  SenateFirstPrefsByDivisionByVoteTypeDownload-31496.csv \
  SenateSenatorsElectedDownload-31496.csv \
  GeneralEnrolmentByStateDownload-31496.csv \
  SenateInformalByStateDownload-31496.csv \
  SenateTurnoutByStateDownload-31496.csv \
  SenateVotesCountedByStateDownload-31496.csv \
  SenateInformalByDivisionDownload-31496.csv \
  SenateTurnoutByDivisionDownload-31496.csv \
  SenateVotesCountedByDivisionDownload-31496.csv \
  SenateFirstPrefsByGroupByVoteTypeDownload-31496.csv \
  SenateFirstPrefsByStateByGroupByVoteTypeDownload-31496.csv \
  SenateDopDownload-31496.zip \
  aec-senate-formalpreferences-31496-ACT.zip; do
  if [ ! -f "$TARGET/data/raw/aec/2025_federal/31496/final/$filename" ]; then
    echo "A required installed workflow-test source is missing:"
    echo "$TARGET/data/raw/aec/2025_federal/31496/final/$filename"
    echo "No change was made."
    exit 1
  fi
done

STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_ROOT="$HOME/Desktop/Politica Backups"
BACKUP="$BACKUP_ROOT/Stage10-before-1.0.1-$STAMP"
mkdir -p "$BACKUP"

for directory in src config docs tests packaging; do
  if [ -d "$TARGET/$directory" ]; then
    /usr/bin/ditto "$TARGET/$directory" "$BACKUP/$directory"
  fi
done
for file in README.md pyproject.toml uv.lock start_politica.command start_politica.bat configure_google_sheets.command; do
  if [ -f "$TARGET/$file" ]; then
    cp -p "$TARGET/$file" "$BACKUP/$file"
  fi
done
mkdir -p "$BACKUP/data/app/releases"
cp -p "$TARGET/data/app/releases/active.json" "$BACKUP/data/app/releases/active.json"

echo "Application-code and active-pointer backup created:"
echo "$BACKUP"
echo
echo "Installing Stage 10.1 code. Existing releases and raw sources are not being edited..."

for directory in src config docs tests packaging; do
  /usr/bin/ditto "$PAYLOAD/$directory" "$TARGET/$directory"
done
for file in .gitignore README.md pyproject.toml uv.lock start_politica.command start_politica.bat configure_google_sheets.command; do
  cp -p "$PAYLOAD/$file" "$TARGET/$file"
done
mkdir -p "$TARGET/dist"
for report in stage_10_1_build_manifest.json stage_10_1_test_report.json stage_10_1_integration_report.json; do
  cp -p "$PAYLOAD/dist/$report" "$TARGET/dist/$report"
done
chmod +x "$TARGET/start_politica.command" "$TARGET/configure_google_sheets.command"

cd "$TARGET"
echo
echo "Refreshing the locked Python environment..."
uv sync --locked --python 3.12

echo
echo "Running the complete Stage 4–10.1 regression suite..."
echo "The DOP and ballot test can be quiet for several minutes; wait for the final OK."
uv run python -m unittest -v \
  tests.test_stage4_workflow \
  tests.test_stage5_workflow \
  tests.test_stage6_workflow \
  tests.test_stage7_workflow \
  tests.test_stage8_workflow \
  tests.test_stage9_explorer \
  tests.test_stage9_1_correction \
  tests.test_stage10_publication \
  tests.test_stage10_1_person_links

echo
echo "Creating the corrected release from an isolated copy of the active immutable release..."
echo "The old release remains unchanged. The active pointer moves only after the copy validates."
uv run politica-erd-correct-person-links

echo
echo "Validating the newly active checksum-pinned immutable release..."
uv run politica-erd-validate

echo
echo "Stage 10.1 v1.0.1 is installed, corrected and verified."
echo "Werriwa and Solomon now carry their authoritative person links."
echo "Your prior release, raw sources, Google Sheets configuration, credentials and Grand Database remain unchanged."
echo "Start Politica with:"
echo "cd \"$TARGET\""
echo "./start_politica.command"
