# Review Configuration Placeholders

This directory hosts the configuration that steers the review workflow. The CLI reads these
files automatically when you run `scripts/review_process_workflow.py`.

- `logic_profiles.yaml` — defines executable profiles (`default`, `iso_14071_critical_review`,
  `pef_verification_validation`, `combined_iso_pef`) along with the check list and extra field
  definition files to load.
- `field_definitions_iso.yaml` / `field_definitions_pef.yaml` — schema constraints used by the
  `field_content` check to enforce ISO 14071 and PEF Annex 8 requirements.
- `scope_method_map.yaml` — maps dataset UUIDs or profile names to the TIDAS-compliant review
  scope and method set; the CLI falls back to `default` if no specific entry is found.
- `templates.yaml` — maps profiles to context files that populate the generated Markdown/DOCX
  report. No external Word 模板 is required; the CLI renders DOCX directly via python-docx.
