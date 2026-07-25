# Data manifest fields

Every external artifact record must include:

- exact filename;
- persistent provider and file identifier or stable location;
- release version and release ID;
- purpose;
- size in bytes;
- SHA-256 checksum;
- immutable or mutable classification;
- required-for-continuation flag;
- dependency relationships;
- verification status and verification date.

An external folder name without file identifiers and checksums is not a complete manifest.

