# Deployment patch status — 3 October 2026

Branch: `deploy/streamlit-fast-finance`, based on `f816722` of
`feature/multiyear-plants-and-tiered-sla`. Deployment instructions and first-test
steps are in [STREAMLIT_TESTING.md](STREAMLIT_TESTING.md).

## Delivered behaviour

- Fast mode defaults to one PyPSA dispatch year and 25 financial projection years.
  Financial records repeat energy quantities and apply the relevant finance
  assumptions; hourly results are stored and exported once. The projection
  horizon controls fast-mode summary cashflows and appraisal life.
- Full multi-year dispatch remains selectable. Degradation is disabled in fast
  mode because future dispatch is not solved. Capacity sizing remains optional
  and uses the selected historical year, independently of the finance horizon.
- One solve slot per app process prevents overlapping testers from launching
  concurrent native solvers. Serial dispatch is the default.
- The shipped single-year data picker works; edited terms clear old outputs and
  refresh links; malformed, expired and invalid-path run links fail gracefully.
- The tsam extreme-period occurrence alignment bug is corrected, including the
  previously failing daily-SLA clustering case. Unaffected 40-week aggregation
  matches the pinned upstream implementation.
- Dispatch revenue includes spreads and certificate assumptions. Bundled market
  purchases pay the assumed certificate price. Detailed finance and workbook
  cashflows now deduct purchases and transmission costs; subhourly MWh are
  integrated correctly; storage discharge does not count as new generation.
- Python 3.13 installation and test instructions are supplied, along with a
  GitHub Actions test workflow.

## Verification

Pinned runtime: Python 3.13.15, PyPSA 1.2.4, HiGHS/highspy 1.15.0,
Streamlit 1.57.0 and tsam 3.4.2.

| Check | Result |
|---|---|
| Main regression suite, excluding timeout-probe tests | 463 passed, 1 optional skip; 169 seconds |
| Four timeout-probe tests | 4 passed; 6 seconds |
| Finance checks after the projection-horizon correction | 38 passed, including 4 additional horizon cases |
| Streamlit app initial render | Passed |
| Plant picker, terms, run, delivery, finance and sensitivity render with actual results | All passed |
| Financial-model button using the real result | Passed |
| Real 2025 ARWF1 / AVLSF1 / NSW1 app run, sizing off | Optimal; one 8,760-hour result and 25 annual financial records |
| Detailed finance using that result | 25 operating years |
| Financial and hourly XLSX export/read-back | Passed; hourly sheet contains one year |

The real-year app run took **3.77 seconds** after the benchmark process imported
its prerequisites. Export and workbook read-back were checked afterwards. Peak
resident memory for the benchmark process, including export/read-back, was
approximately **747 MiB**. These are measurements in this workspace, not a
Community Cloud performance guarantee. Capacity sizing was disabled in this
benchmark; it is a separate and slower solve. The baseline pre-patch review
recorded a successful 40-week sizing run, and this patch checks its aggregation
compatibility without claiming a new cloud sizing benchmark.

The timeout tests required a test-only namespace-aware PID lookup because this
workspace exposes host `/proc` process IDs while Python returns namespace IDs.
Production timeout code was not changed for that workspace peculiarity. The
optional skipped test requires an Excel-formula evaluator absent from the
runtime; workbook structure, formula links and Python finance were checked.

No live Community Cloud deployment or manual browser UAT has been completed.
The next check is Matt's deployment of this branch using Python 3.13, hourly
inputs, fast mode and fixed capacities. Use the testing guide to verify delivery,
finance assumptions, exports and refresh before enabling capacity sizing.

## Interpretation

Fast mode is a fixed-volume projection, not 25 independently optimised years.
It does not model degradation, weather variation, augmentation or future price
responses in dispatch. A repeated delivery percentage is a projection, not
validation of future SLA compliance. The simplified appraisal and detailed
levered model use different financial assumptions; compare those assumptions
before comparing returns. Market purchases are certificate-backed in the
economic model, without physical renewable provenance tracking.
