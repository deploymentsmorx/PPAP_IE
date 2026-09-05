# Standards

This folder separates standard-specific configuration and assets from the shared
pipeline code.

- `aiag_ppap` contains the current AIAG PPAP tagging and validation assets.
- `vda_ppf` is the scaffold for VDA Volume 2 PPF/PPA support.

Shared upload, extraction, model access, persistence, review, and report
rendering code should stay in the existing backend packages unless a standard
needs a truly different implementation.

