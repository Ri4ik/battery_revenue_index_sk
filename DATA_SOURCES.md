# Data sources and public release status

Reviewed 2026-10-20. This document records provenance and unresolved permissions;
it does not grant a license to third-party data. A public API or freely downloadable
file does not, by itself, establish permission to republish it or derived products.

| Source | Used for | Reference | Permission status |
| --- | --- | --- | --- |
| OKTE DAM, IDM, IDA, ISZO | Slovak energy and imbalance inputs | https://www.okte.sk/en/ | Terms applicable to the exact inputs and derived public charts still need confirmation. |
| SEPS / Damas reports | FCR and aFRR capacity inputs | https://www.sepsas.sk/en/ | Terms applicable to the exact reports and derived public charts still need confirmation. |
| ENTSO-E Transparency Platform | Additional experiments; possible historical input provenance | https://transparency.entsoe.eu/ | Verify the exact series, primary data owner, date and applicable reuse list before redistribution. |

ENTSO-E publishes a list of data available for reuse under CC-BY 4.0, with exceptions.
Do not assume that every dataset on the platform has the same permission:
https://transparency.entsoe.eu/content/static_content/Static%20content/terms%20and%20conditions/220218_List_of_Data_available_for_reuse.pdf

Before a public release, the project maintainer must record the applicable terms or
permission reference for each used input, required attribution, permitted derived
outputs, and the review date. This is an outstanding release condition, not an
assertion that permission is either granted or forbidden. No permission request is
sent automatically by this repository.

The first release selects only `Slovakia_2025-2026 (01.03)` and its 1/2-hour,
1/2-cycle variants. It exposes daily calculated results, not downloadable raw inputs.
This boundary does not substitute for checking permissions for those results.

Historical range: 2025-03-01 through 2026-03-01 inclusive (366 dates), 8,055 result files.
The 1-hour/1-cycle variant contains DA, ID1, IDA1, IDM15, IDM60, IMB and aFRR (366 each).
The other three contain DA, IDM15, IDM60, IMB and aFRR (366 each) and FCR only on
2025-03-01. Missing days must not be described as zero revenue.

Market codes are preserved without merging. In particular `aFRR` represents the
historical JSON Total; this release does not recalculate or relabel it as purely
capacity revenue. Independent single-market scenarios cannot be added together as
the achievable revenue of one battery. Results are historical theoretical benchmarks,
not forecasts, trading advice, or promises of actual profit.
