# Manual smoke-test checklist

Use this after changing the app, data loading, or exports. Run `streamlit run streamlit_app.py` from the repository root.

- [ ] App starts and the main navigation renders without an exception.
- [ ] A bundled plant can be selected and its availability data is visible.
- [ ] A bundled case can complete a short, fixed-capacity optimisation.
- [ ] Results and financial views render from the completed run.
- [ ] Financial workbook export downloads and opens without a repair prompt.
- [ ] Invalid or incomplete user inputs produce a clear message and do not crash the app.
- [ ] A run with optional cache files missing follows the documented fallback rather than failing at startup.

Record the scenario, affected screen, error output, and a screenshot for any failure. Numerical changes should be checked against the automated model tests and, where sizing behavior changes, the sizing benchmark scripts.
