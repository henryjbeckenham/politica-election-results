# Stage 13.1 package

Use `install_stage13_1.command` at the top of the extracted update folder. The installer accepts application 1.3.0 or 1.3.1, creates a Desktop backup, installs the path-safe non-editable Python package, runs the complete Stage 4–13.1 regression suite, validates the unchanged active election release, verifies the official Senate snapshot and builds a new static website package.

Node.js is not required on the operator's Mac. The tested Observable site is already compiled in `src/politica_erd/app/results`.
