# Stage 12 website publication guide

## What Stage 12.0 does

Stage 12.0 creates a complete public website package on the local computer. It
does not place the website online.

The package includes:

- the compiled public election-results interface;
- seven fixed JSON feeds;
- seven matching CSV feeds;
- feed and release manifests;
- checksums for every packaged file.

It excludes the DuckDB database, raw source files, credentials, Google Sheets
configuration and every operator or ingestion control.

## Build a package

1. Start Politica normally.
2. Open **Website publication** in the left navigation.
3. Select **Build website package**.
4. Keep Politica open until the status becomes **Ready to deploy**.
5. Select **Preview package** to inspect the self-contained static version.
6. Select **Download ZIP** to save the verified hosting package.

Nothing is uploaded by these actions.

## When the database changes

After a new election release is activated, the Website publication page marks
the earlier package as **Update required**. Select **Build updated package**.
The old package remains unchanged and the new package receives its own release
identity.

## Terminal alternative

From the Politica project folder:

```bash
uv run politica-erd-build-public-site
```

To inspect the active website-package status:

```bash
uv run politica-erd-build-public-site --status
```

The generated ZIP is stored beneath `data/app/public_website/exports`. Use the
operator download button for the simplest access.

## Stage 12.1

Stage 12.1 will connect this verified package workflow to a selected static
hosting account and public domain. Hosting credentials are not required for
Stage 12.0 and must not be placed inside the website ZIP.
