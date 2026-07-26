# Politica public election-results site

This Observable Framework project builds the public, read-only election-results interface included in application 1.7.0. Stage 14.5 retains the House and Senate visualisation suites and provides governed election selection across the 2025, 2022, 2019, 2016 and 2013 federal elections. Detailed count feeds load only when the Senate route is opened, while the site remains self-contained and static-publication compatible.

The browser reads only fixed Stage 10 publication contracts plus the checksum-governed AEC boundary GeoJSON. It never opens DuckDB directly and exposes no arbitrary SQL or write operation. The production build and governed geometry are copied into `src/politica_erd/app/results` and served by FastAPI at `/results/`.

Development commands:

- `npm install`
- `npm test`
- `npm run build:app`

The installed Politica application does not require Node.js. It serves the prebuilt files included in the Python package.
