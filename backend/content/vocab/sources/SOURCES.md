# Word-list sources

These CSV files are unmodified copies of the official downloads. They are licensed under
[Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0)](https://creativecommons.org/licenses/by-sa/4.0/),
not under this repository's MIT license.

| File | List | Version | Official page | Download URL (redirect target) | Retrieved | SHA-256 |
|---|---|---|---|---|---|---|
| `TSL_12_stats.csv` | TOEIC Service List (TSL), with basic statistics | 1.2 | https://www.newgeneralservicelist.com/toeic-service-list | https://www.newgeneralservicelist.com/s/TSL_12_stats.csv → https://static1.squarespace.com/static/64336926d7c6bb38965fdf3b/t/643c876e097db81d6db2722c/1681688430425/TSL_1.2_stats.csv | 2026-11-12 | `2259df25a1077b04067ef0c0e9e4c98b46c91e4d539741dfaceba88651ff486d` |
| `NGSL_12_stats.csv` | New General Service List (NGSL), with basic statistics | 1.2 | https://www.newgeneralservicelist.com/new-general-service-list | https://www.newgeneralservicelist.com/s/NGSL_12_stats.csv → https://static1.squarespace.com/static/64336926d7c6bb38965fdf3b/t/644e0be4ad7bae3d45b9e62a/1682836452194/NGSL_1.2_stats.csv | 2026-11-12 | `2098bab8955a120a9766c6282a51d7d578c6cb0a7d946600d2ffb73ba25a0b44` |

Attribution (as requested by the authors):

- Browne, C., Culligan, B., & Phillips, J. *The New General Service List* (NGSL 1.2). https://www.newgeneralservicelist.com
- Browne, C., & Culligan, B. *The TOEIC Service List* (TSL 1.2). https://www.newgeneralservicelist.com

## What ListenFlow takes from them

Only the headword, the list it comes from, its rank and its SFI (Standard Frequency Index).
`backend/content/vocab/ngsl_tsl.json` was built from these columns: TSL ranks 1–520 and NGSL ranks 1001–1520,
single-word headwords of 3+ letters, minus words already in `toeic_core.json`.
NGSL ranks 1–1000 are left out because they are mostly function words and very basic vocabulary.

Because that file is an adaptation of the lists, it is distributed under **CC BY-SA 4.0** as well.
The Japanese glosses, usage scenes, example sentences and translations in it were written for
ListenFlow. They are not taken from the NGSL/TSL definition files, commercial word books, dictionaries or
official TOEIC materials.

The lists give no Japanese meanings, parts of speech or difficulty bands. `pos`, `meaning`, `tier`,
`topic` and `scene` in `ngsl_tsl.json` were assigned by the ListenFlow authors. `tier` is not CEFR, and
`rank`/`sfi` are the list authors' corpus statistics, not TOEIC test statistics.
