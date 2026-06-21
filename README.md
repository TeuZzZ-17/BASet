# BASet

Microsoft Urban Assault SET.BAS Extractor - Converter
This is a read-only tool for exploring and extracting data from *Urban Assault* / *OpenUA* `SET.BAS` files.

The project is intended to support preservation, research, and future modding tools by making the contents of `SET.BAS` files easier to inspect.

<img width="643" height="427" alt="Screenshot 2026-06-20 003542" src="https://github.com/user-attachments/assets/f5933c88-5953-4757-bfd3-cef38902b468" />

## Purpose

`SET.BAS` files contain game data used by *Urban Assault*, including embedded assets and structural information.

BASet provides a safer and more convenient way to inspect this data without modifying the original files.

## Current capabilities

BASet currently focuses on:

- Extracting embedded data from `SET.BAS`
- Exporting selected raw resources
- Converting supported texture formats
- Exporting structural metadata for research and tooling
- Providing optional developer-oriented raw dumps for deeper analysis

The tool is still evolving, so specific workflows, output folders, and supported formats may change over time.

## Extract output

The main Extract workflow writes `manifest.json` and raw data folders:

```text
raw/
  VBMP/
  SKLT/
  ANM/
  BASE_KIDS/
```

`VBMP`, `SKLT`, and `ANM` contain embedded EMRS asset payloads.

`BASE_KIDS` preserves raw BASE/KIDS structural scene graph data, including top-level `FORM KIDS`, immediate `FORM OBJT` children, and important structural leaf chunks such as `CLID`, `NAME`, `NAM2`, `STRC`, `ATTS`, `OLPL`, and `OTL2`.

`ATTS`, `OLPL`, `OTL2`, and `STRC` are preserved as binary chunks for later analysis; BASet does not decode them deeply yet.

## Read-only design

BASet is designed to be read-only.

It does not modify the original `SET.BAS` file.

## Intended use

BASet is mainly intended for:

- Urban Assault / OpenUA research
- Asset inspection
- Modding tool development
- Format analysis
- Future editor and viewer workflows

## Legal note

BASet does not include or redistribute original *Urban Assault* game assets.

Users must provide their own legally obtained game files.

*Urban Assault* and all original game assets remain property of their respective rights holders.

## License

This project is released under the GPL-3.0 license.

See `LICENSE` for details.
