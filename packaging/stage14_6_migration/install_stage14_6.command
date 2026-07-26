#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PAYLOAD="$SCRIPT_DIR/payload"
ASSET_PARENT=$(dirname "$SCRIPT_DIR")
TARGET=${1:-"$HOME/Downloads/Politica_Election_Results_Database"}
BACKUP=""
ASSET_STAGE=""

DATA_1="Politica_Stage14_6_v1.8.0_Data_1_NSW.zip"
DATA_2="Politica_Stage14_6_v1.8.0_Data_2_VIC_SA.zip"
DATA_3="Politica_Stage14_6_v1.8.0_Data_3_QLD_WA.zip"
DATA_4="Politica_Stage14_6_v1.8.0_Data_4_ACT_NT_TAS_and_counts.zip"
DATA_1_SHA256="b387adecc4810c5449a8fba9e308b23840f1a30bf7257a7af7a53e0f7683f5f1"
DATA_2_SHA256="1f837b3776ee58e2b7a7f6fbb347b8bd7ea9d459b0e559f1f4ce3236329c38f8"
DATA_3_SHA256="e9ffa545a3427dbbfe5376f0ad5f05a4cfbe9f4695bb014bc545f6e109d3346d"
DATA_4_SHA256="054db5b606b2de5819ca852b1354daa383261608f00f3938905de22dbff27ad5"

on_exit() {
  status=$?
  if [ -n "$ASSET_STAGE" ] && [ -d "$ASSET_STAGE" ]; then
    rm -rf -- "$ASSET_STAGE"
  fi
  if [ "$status" -ne 0 ]; then
    echo
    echo "Stage 14.6 stopped before every installation check passed."
    echo "No prior immutable election release or original AEC source was deleted or overwritten."
    echo "No website was uploaded or externally deployed."
    if [ -n "$BACKUP" ]; then
      echo "The pre-update application-code and active-pointer backup is at:"
      echo "$BACKUP"
    fi
  fi
}
trap on_exit EXIT

for required in \
  src/politica_erd/install_2010_release.py \
  src/politica_erd/verify_stage14_6_install.py \
  src/politica_erd/app/publication.py \
  src/politica_erd/app/visualisations.py \
  src/politica_erd/app/results/index.html \
  src/politica_erd/app/results/data/boundaries/house_electorates_2010.geojson \
  schema/005_stage14_6_2010.sql \
  config/source_catalogue_historical.yml \
  config/source_checksums_2010.yml \
  config/electorate_boundaries_2010.yml \
  config/boundaries/source/national-esri-2010.zip \
  config/boundaries/derived/house_electorates_2010.geojson \
  data/manifests/aec_2010_sources.json \
  data/manifests/aec_2010_formal_preferences.json \
  data/manifests/aec_2010_parquet.json \
  data/manifests/aec_2010_delta_tables.json \
  dist/stage_14_6_2010_import_report.json \
  dist/stage_14_6_integration_report.json \
  dist/stage_14_6_test_report.json \
  docs/release_notes_1.8.0.md \
  docs/stage_14_6_operator_guide.md \
  pyproject.toml \
  uv.lock; do
  if [ ! -e "$PAYLOAD/$required" ]; then
    echo "The Stage 14.6 core payload is incomplete. Extract the complete update ZIP before running this file."
    echo "Missing: $PAYLOAD/$required"
    exit 1
  fi
done

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

for required in \
  pyproject.toml \
  src/politica_erd/app/service.py \
  data/app/releases/active.json \
  config/source_catalogue_historical.yml \
  config/source_checksums_2013.yml \
  docs/stage_14_5_operator_guide.md; do
  if [ ! -e "$TARGET/$required" ]; then
    echo "This is not the tested Stage 14.5 Politica installation:"
    echo "$TARGET"
    echo "Missing: $required"
    exit 1
  fi
done

if ! grep -Eq '^version = "1\.(7\.0|8\.0)"$' "$TARGET/pyproject.toml"; then
  echo "Stage 14.6 expects application version 1.7.0 with Stage 14.5 installed."
  echo "No change was made."
  exit 1
fi

if command -v lsof >/dev/null 2>&1 && lsof -tiTCP:8765 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Politica is still running on port 8765."
  echo "Return to its Terminal window, press Control-C once, then run this installer again."
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "The uv command is unavailable. The existing Politica installation cannot be updated safely."
  exit 1
fi

ASSET_STAGE=$(mktemp -d "${TMPDIR:-/tmp}/politica-stage14-6-assets.XXXXXX")
mkdir -p "$ASSET_STAGE/stage14_6_assets"

extract_asset() {
  filename=$1
  expected=$2
  stem=${filename%.zip}
  archive=""
  for candidate in "$SCRIPT_DIR/$filename" "$ASSET_PARENT/$filename"; do
    if [ -f "$candidate" ]; then
      archive=$candidate
      break
    fi
  done
  if [ -n "$archive" ]; then
    observed=$(/usr/bin/shasum -a 256 "$archive" | awk '{print $1}')
    if [ "$observed" != "$expected" ]; then
      echo "A Stage 14.6 data package failed checksum verification:"
      echo "$archive"
      exit 1
    fi
    /usr/bin/ditto -x -k "$archive" "$ASSET_STAGE"
    return
  fi

  for directory in \
    "$SCRIPT_DIR/$stem/stage14_6_assets" \
    "$ASSET_PARENT/$stem/stage14_6_assets" \
    "$SCRIPT_DIR/$stem" \
    "$ASSET_PARENT/$stem"; do
    if [ -d "$directory/data" ]; then
      /usr/bin/ditto "$directory" "$ASSET_STAGE/stage14_6_assets"
      return
    fi
  done

  echo "A required Stage 14.6 data package was not found:"
  echo "$filename"
  echo "Keep all four data ZIPs beside the extracted core update folder in Downloads."
  exit 1
}

echo "Verifying the four prevalidated 2010 data packages..."
extract_asset "$DATA_1" "$DATA_1_SHA256"
extract_asset "$DATA_2" "$DATA_2_SHA256"
extract_asset "$DATA_3" "$DATA_3_SHA256"
extract_asset "$DATA_4" "$DATA_4_SHA256"

ASSET_ROOT="$ASSET_STAGE/stage14_6_assets"
RAW_ROOT="$ASSET_ROOT/data/raw/aec/2010_federal/15508/final"
PARQUET_ROOT="$ASSET_ROOT/data/parquet/aec_2010"
TABLE_ROOT="$ASSET_ROOT/data/stage14_6/tables"
if [ ! -d "$RAW_ROOT" ] || [ ! -d "$PARQUET_ROOT" ] || [ ! -d "$TABLE_ROOT" ]; then
  echo "The Stage 14.6 data packages did not reconstruct the governed asset tree."
  exit 1
fi
RAW_COUNT=$(find "$RAW_ROOT" -type f ! -name '*.part' | wc -l | tr -d ' ')
PARQUET_COUNT=$(find "$PARQUET_ROOT" -type f -name '*.parquet' | wc -l | tr -d ' ')
TABLE_COUNT=$(find "$TABLE_ROOT" -type f -name '*.parquet' | wc -l | tr -d ' ')
if [ "$RAW_COUNT" -ne 47 ] || [ "$PARQUET_COUNT" -ne 126 ] || [ "$TABLE_COUNT" -ne 56 ]; then
  echo "The Stage 14.6 data package inventory is incomplete."
  echo "Observed $RAW_COUNT AEC sources, $PARQUET_COUNT fact/ballot files and $TABLE_COUNT relational shards; expected 47, 126 and 56."
  exit 1
fi

STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_ROOT="$HOME/Desktop/Politica Backups"
BACKUP="$BACKUP_ROOT/Stage14.5-before-1.8.0-$STAMP"
mkdir -p "$BACKUP"
for directory in src config docs tests packaging visualisation; do
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
if [ -f "$TARGET/data/app/public_website/active.json" ]; then
  mkdir -p "$BACKUP/data/app/public_website"
  cp -p "$TARGET/data/app/public_website/active.json" "$BACKUP/data/app/public_website/active.json"
fi

echo "Application-code and active-pointer backup created:"
echo "$BACKUP"
echo
echo "Installing application 1.8.0 and the prevalidated 2010 release assets..."

if [ -d "$TARGET/src/politica_erd/app/results" ]; then
  mv "$TARGET/src/politica_erd/app/results" "$BACKUP/preinstall-results-site"
fi
if [ -d "$TARGET/visualisation/dist" ]; then
  mv "$TARGET/visualisation/dist" "$BACKUP/preinstall-visualisation-dist"
fi

for directory in src config docs tests packaging visualisation schema scripts; do
  /usr/bin/ditto "$PAYLOAD/$directory" "$TARGET/$directory"
done
for file in .gitignore README.md pyproject.toml uv.lock start_politica.command start_politica.bat configure_google_sheets.command; do
  if [ -f "$PAYLOAD/$file" ]; then
    cp -p "$PAYLOAD/$file" "$TARGET/$file"
  fi
done
cp -p "$SCRIPT_DIR/install_stage14_6.command" "$TARGET/packaging/install_stage14_6.command"

mkdir -p "$TARGET/data/manifests" "$TARGET/data/stage14_6" "$TARGET/dist"
/usr/bin/ditto "$PAYLOAD/data/manifests" "$TARGET/data/manifests"
/usr/bin/ditto "$PAYLOAD/dist" "$TARGET/dist"
mkdir -p "$TARGET/data/raw/aec" "$TARGET/data/parquet"
/usr/bin/ditto "$ASSET_ROOT/data/raw/aec/2010_federal" "$TARGET/data/raw/aec/2010_federal"
/usr/bin/ditto "$ASSET_ROOT/data/parquet/aec_2010" "$TARGET/data/parquet/aec_2010"
/usr/bin/ditto "$ASSET_ROOT/data/stage14_6" "$TARGET/data/stage14_6"
chmod +x "$TARGET/start_politica.command" "$TARGET/configure_google_sheets.command"

cd "$TARGET"
POLITICA_PROJECT_ROOT="$TARGET"
export POLITICA_PROJECT_ROOT

echo
echo "Refreshing the locked Python environment..."
uv sync --locked --python 3.12 --no-editable

echo
echo "Verifying the packaged AEC sources and creating the immutable combined 2025, 2022, 2019, 2016, 2013 and 2010 release..."
uv run --no-sync python -m politica_erd.install_2010_release

echo
echo "Running the short six-election post-installation smoke check..."
uv run --no-sync python -m politica_erd.verify_stage14_6_install

echo
echo "Building and verifying the six-election static website package..."
uv run --no-sync python -m politica_erd.static_site

echo
echo "Stage 14.6 v1.8.0 is installed and verified."
echo "The application now contains the complete governed 2025, 2022, 2019, 2016, 2013 and 2010 federal election results."
echo "The prior 2025, 2022, 2019, 2016 and 2013 database releases and all earlier immutable releases remain unchanged."
echo "The official 13-paper gap between the 2010 non-ticket aggregate and published BTL matrices is disclosed without fabricating ballot records."
echo "No full regression suite was rerun on this Mac; only installation and release smoke checks were performed."
echo "Start Politica with:"
echo "cd \"$TARGET\""
echo "./start_politica.command"
echo "Then open: http://127.0.0.1:8765/results/"
