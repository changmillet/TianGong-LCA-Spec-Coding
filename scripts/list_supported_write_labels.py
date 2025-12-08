#!/usr/bin/env python
"""List all `ui_label` values supported by the write-process workflow."""

from __future__ import annotations

from tiangong_lca_spec.process_update.updater import FIELD_MAPPINGS


def main() -> None:
    mappings = sorted(FIELD_MAPPINGS.values(), key=lambda item: item.label)
    for mapping in mappings:
        print(mapping.label)


if __name__ == "__main__":
    main()
