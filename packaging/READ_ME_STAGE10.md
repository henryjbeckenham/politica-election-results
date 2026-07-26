# Politica Stage 10 v1.0.0 update

Stage 10 adds versioned, read-only JSON/CSV visualisation feeds and provenance manifests. It does not replace or migrate your active election database.

## Before installing

If Politica is running, return to its Terminal window and press **Control-C once**. Wait until the server stops.

## Install on your Mac

Open a new Terminal window and run these commands exactly:

```bash
cd ~/Downloads/Politica_Stage10_v1.0.0_Update
chmod +x install_stage10.command
./install_stage10.command
```

The installer creates an application-code backup on your Desktop, refreshes the locked environment, runs all 47 Stage 4–10 tests, validates the active immutable release, and confirms that the active pointer did not change. The official DOP/formal-ballot test can be quiet for several minutes; wait for the final success message.

## Start Politica after success

```bash
cd ~/Downloads/Politica_Election_Results_Database
./start_politica.command
```

Open **Visualisation feeds** in the left navigation. Choose an election and optional state, then copy a JSON URL, download CSV, or open its manifest.

## Preserved unchanged

- `data/app`, including the active and historical releases;
- all raw election sources and Parquet ballot data;
- the Google Sheets service-account configuration and `.env`; and
- the Grand Database.
