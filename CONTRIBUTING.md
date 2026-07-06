# Contributing to Tour de France Live Tracker

Thanks for your interest in contributing! 🚴

## 🐛 Reporting bugs

Open a [GitHub Issue](https://github.com/DasClown/tour-de-france-live-tracker/issues)
with:

1. **What happened** (expected vs. actual behavior)
2. **Steps to reproduce** — ideally a concrete stage/year + curl command
3. **Logs** — `journalctl -u tdf-tracker -n 50` or the relevant output
4. **Environment** — Python version, OS, stage/year, race status (live vs. ended)

Use the issue templates in `.github/ISSUE_TEMPLATE/` if available.

## 💡 Suggesting features

Open an issue with the `enhancement` label. Areas that need love:

- Time-cut detection (post-race)
- Real elevation profile from polyline (currently CSV best-effort)
- Runtime config changes (stage/port) without restart
- Frontend tests (Playwright)
- WebSocket reconnect logic
- Multi-race support (La Vuelta, Giro — same ASO infrastructure?)

## 🔧 Submitting code

1. **Fork** the repo and create a branch:
   ```bash
   git checkout -b fix/my-bugfix     # or: feat/my-feature
   ```
2. **Run the tests** before and after your changes:
   ```bash
   pytest -m "not e2e"   # ~3s, no live server needed
   ```
   All tests must stay green. If you add a feature, add tests.
3. **Follow the existing style**:
   - Python 3.10+ syntax (`from __future__ import annotations`)
   - Dataclasses for state, async where the codebase is async
   - Type hints everywhere
   - German comments/docstrings are okay (the original is bilingual)
4. **Commit messages**: present tense, concise
   (`Fix withdrawal cooldown`, not `Fixed withdrawal cooldown`)
5. **Open a Pull Request** against `main`. Reference the issue if applicable.

## 🧪 Test structure

| Stage | What | Marker |
|---|---|---|
| 1 — Unit | Pure functions (math, parsing, logic) | (none) |
| 2 — Integration | bootstrap, state with mocked/local ASO fixtures | (none) |
| 3 — End-to-End | Live server endpoints, auth matrix | `@pytest.mark.e2e` |

The `tests/fixtures/` directory contains real ASO responses (Tour 2026,
stage 2). They enable deterministic offline testing — **do not modify**
unless ASO's schema changes (then re-capture via the snippet in
`tests/conftest.py`).

## 📜 Code of conduct

Be excellent to each other. Cycling fans especially. 🤝
