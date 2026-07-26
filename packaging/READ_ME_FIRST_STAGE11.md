# Politica Stage 11 v1.1.0

This update adds the public-facing Election Results site while preserving the existing operator, active database, immutable releases, raw sources and Google Sheets configuration.

## Install

1. If Politica is running, return to its Terminal window and press **Control-C** once.
2. Keep this extracted folder together.
3. Double-click `install_stage11.command`, or run it from Terminal.
4. Wait for the complete Stage 4–11 test suite and final database validation. It may take approximately 15 minutes.
5. Start Politica normally and open `http://127.0.0.1:8765/results/`.

The installer creates an application-code and active-pointer backup on the Desktop before copying anything. It verifies that the active pointer is byte-for-byte unchanged after installation.
