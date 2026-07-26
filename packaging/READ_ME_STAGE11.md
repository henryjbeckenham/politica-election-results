# Stage 11 v1.1.0 update

Stage 11 adds the user-facing Politica Election Results site at `/results/`.

The installer updates application code and prebuilt web assets only. It does not edit `data/app`, immutable releases, raw election sources, `.env`, Google Sheets credentials or the Grand Database. It records the active-pointer checksum before installation and refuses to report success if that pointer changes.

Before installation, stop the running Politica Terminal with **Control-C**. Then run `install_stage11.command`. The complete Stage 4–11 test suite can take approximately 15 minutes; the DOP and ballot test can remain quiet for several minutes.

After the final `OK`, start Politica normally and open `http://127.0.0.1:8765/results/`.
