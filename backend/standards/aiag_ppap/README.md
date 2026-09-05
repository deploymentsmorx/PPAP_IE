# AIAG PPAP

Current production behavior lives here as standard-specific assets:

- `tagging/elements.json`
- `tagging/keywords.json`
- `validation/checkpoints/ppap_validation_checkpoints.json`

The existing `backend.tagging` and `backend.validation` modules still provide
the runtime entry points for now; they load these files from this standard
folder.

