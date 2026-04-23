# Release Process

How to ship a new FinTrack version end-to-end: from tag to signed installer on
GitHub Releases. Last verified against Tauri 2.x, PyInstaller 6.x, GitHub
Actions `macos-latest` + `windows-latest` runners (2026-04-22).

---

## Overview

Releases are driven by tags. Pushing a tag matching `v*` triggers
`.github/workflows/release.yml` which:

1. Builds on `macos-latest` (Apple Silicon) and `windows-latest` in parallel
2. Freezes the Python sidecar with PyInstaller (`dist/fintrack-sidecar/`)
3. Builds the Tauri app, which bundles the frozen sidecar as a resource
4. Signs the binaries if signing secrets are present (no-op otherwise)
5. Uploads the installers as workflow artifacts
6. Creates a **draft** GitHub Release with the artifacts attached

Installers produced per platform:

| Platform | Installer | Updater bundle |
|----------|-----------|----------------|
| macOS (aarch64) | `FinTrack_<version>_aarch64.dmg` | `FinTrack.app.tar.gz` + `.sig` |
| Windows (x64)   | `FinTrack_<version>_x64_en-US.msi` | `*.msi.zip` + `.sig` |
|                 | `FinTrack_<version>_x64-setup.exe` | `*-setup.exe.zip` + `.sig` |

The updater bundles are only produced when the updater signing key is set.
Unsigned / un-updater-signed builds still ship full installers — they just
won't auto-update.

---

## Prerequisites

### Local tools you need installed

```bash
# GitHub CLI — used throughout this runbook for tags, run watches, releases
brew install gh                          # macOS
# or: winget install --id GitHub.cli     # Windows

# First-time auth (interactive)
gh auth login                            # pick GitHub.com → HTTPS → browser

# Verify
gh auth status
```

All the `gh` commands below assume you're logged in. If you prefer to stay
in the browser entirely, each `gh` call has a web-UI equivalent noted inline.

### One-time setup (already done for this repo)

- `sidecar.spec` — PyInstaller one-folder spec producing `dist/fintrack-sidecar/`
- `requirements-packaging.txt` — pins PyInstaller on release machines / CI
- `shell/src-tauri/tauri.conf.json` — `bundle.resources` bundles the frozen sidecar, `bundle.createUpdaterArtifacts: true`, `plugins.updater.endpoints` points at the GitHub Releases `latest.json` feed
- `shell/src-tauri/src/lib.rs` — `find_frozen_sidecar()` prefers the bundled binary, falls back to `.venv/bin/python -m sidecar.main` in dev
- `.github/workflows/ci.yml` — runs on every PR + push to main (ruff, mypy, pytest, eslint, vite build)
- `.github/workflows/release.yml` — runs on tag push (build, sign, upload, draft release)

### Generate the updater signing keypair (one-time, do before first signed release)

```bash
# macOS / Linux
pnpm -C shell tauri signer generate -w ~/.tauri/fintrack.key

# Writes:
#   ~/.tauri/fintrack.key          — private key (KEEP SECRET, never commit)
#   ~/.tauri/fintrack.key.pub      — public key (base64, safe to commit)
```

Then:

1. Copy the **public key** (contents of `fintrack.key.pub`) into `shell/src-tauri/tauri.conf.json` at `plugins.updater.pubkey`
2. Copy the **private key** (contents of `fintrack.key`) into GitHub Actions secret `TAURI_SIGNING_PRIVATE_KEY`
3. If you chose a key password during generation, also set `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`

The public key ships inside the installer. The updater plugin verifies each
downloaded update against the public key before installing — this is what
prevents a compromised GitHub account from shipping malicious updates. Losing
the private key means cutting a new key + forcing all users to re-download
from scratch, so back it up (password manager, offline USB, whatever you do
for your other signing keys).

---

## GitHub Actions secrets

All secrets are optional — the workflow produces unsigned builds with warnings
when they're unset. Cut v0.1.0 unsigned, then add secrets as you acquire them
(e.g. Apple Developer account paperwork takes ~1–2 business days).

### Apple Developer ID (Mac signing + notarisation)

| Secret | Description |
|--------|-------------|
| `APPLE_CERTIFICATE` | Base64-encoded `.p12` of the "Developer ID Application" cert |
| `APPLE_CERTIFICATE_PASSWORD` | Password set when exporting the `.p12` from Keychain Access |
| `APPLE_SIGNING_IDENTITY` | e.g. `"Developer ID Application: Jane Doe (TEAMID)"` — exact string from `security find-identity -v -p codesigning` |
| `APPLE_ID` | Apple ID with access to notarytool (usually your dev account email) |
| `APPLE_PASSWORD` | App-specific password, **not** your Apple ID password. Generate at [appleid.apple.com](https://appleid.apple.com) → Sign-In & Security → App-Specific Passwords |
| `APPLE_TEAM_ID` | 10-character team ID from [developer.apple.com/account](https://developer.apple.com/account) → Membership |

To encode the `.p12` for the secret:

```bash
base64 -i DeveloperID.p12 | pbcopy   # macOS copies to clipboard
```

The workflow imports the cert into an ephemeral keychain per build, so no
persistent state is left on the GitHub runner. Tauri's bundler reads
`APPLE_SIGNING_IDENTITY` and invokes `codesign` + `notarytool` automatically.

### Windows code signing

Two paths — pick one:

**Option A — EV code-signing cert (USB HSM or cloud-backed):**

| Secret | Description |
|--------|-------------|
| `WINDOWS_CERTIFICATE` | Base64-encoded `.pfx` |
| `WINDOWS_CERTIFICATE_PASSWORD` | Password for the `.pfx` |

Then set `bundle.windows.certificateThumbprint` in `tauri.conf.json` to the
thumbprint printed by the workflow's "Import Windows signing certificate"
step.

**Option B — Azure Trusted Signing (preferred for new setups, ~$10/month):**

Requires additional env vars (`AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, etc.) and
the `Azure.CodeSigning.Dlib` trusted-signing MSIX. Not wired up in this repo
yet; see [Microsoft's docs](https://learn.microsoft.com/en-us/azure/trusted-signing/)
when ready.

### Tauri updater signing

| Secret | Description |
|--------|-------------|
| `TAURI_SIGNING_PRIVATE_KEY` | Private key contents (generated above) |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | Optional — only if you set a password when generating |

Without these, the workflow detects the missing key and invokes
`pnpm tauri build --config '{"bundle":{"createUpdaterArtifacts":false}}'`
instead — installers still build, but no `.tar.gz` / `.zip` updater artefacts
are produced, and the updater is effectively disabled. This is the right
shape for the first release (v0.1.0): nothing exists to auto-update from,
so the updater signing key is optional on day one.

For **local** unsigned bundles (outside CI), use the dedicated script:

```bash
pnpm -C shell tauri:build:unsigned
```

It's the same `--config` override the workflow applies in the unsigned
branch. Plain `pnpm -C shell tauri build` without the key set will abort
with `A public key has been found, but no private key. Make sure to set
TAURI_SIGNING_PRIVATE_KEY environment variable.`

Once the key is added to secrets, subsequent releases ship full updater
bundles automatically — no workflow changes needed.

---

## Cutting a release

### 1. Pre-flight

```bash
# Ensure main is green on CI
gh run list --workflow ci.yml --branch main --limit 1

# Sync versions across files (all should match the tag you're about to cut)
grep -n version shell/src-tauri/tauri.conf.json   # "version": "0.1.0"
grep -n '^version' shell/src-tauri/Cargo.toml     # version = "0.1.0"
grep -n '^version' pyproject.toml                 # version = "0.1.0"
```

If any are out of sync, bump them in a single commit first:

```bash
git commit -am "chore(release): bump to v0.1.0"
git push
```

### 2. Tag + push

```bash
git tag -a v0.1.0 -m "FinTrack v0.1.0"
git push origin v0.1.0
```

This kicks off `release.yml`. Watch the run:

```bash
gh run watch
# or open in browser:  https://github.com/MasimbaTakudzwa/FinTrack/actions
```

Expected duration: ~8–12 minutes (sidecar freeze + Rust build dominates).

### 3. Promote the draft release

The workflow creates a draft release with artifacts attached. Inspect it:

```bash
gh release view v0.1.0
# or open in browser:  https://github.com/MasimbaTakudzwa/FinTrack/releases
```

- Verify the `.dmg`, `.msi`, and `-setup.exe` are present and roughly the
  expected size (~60–90 MB each — the frozen sidecar contributes ~50 MB)
- Download and smoke-test on a clean VM (not the dev machine — the dev
  machine's `.venv` / DB state can mask packaging bugs)
- Edit the release notes if auto-generated text needs polish

When ready, publish:

```bash
gh release edit v0.1.0 --draft=false
# or in browser: Releases page → "v0.1.0" → Edit → uncheck "Set as a pre-release"
# and uncheck "Save as draft" → Publish release
```

---

## Smoke test checklist

On a **clean machine** (fresh VM or a machine that has never run FinTrack):

- [ ] Installer runs without SmartScreen / Gatekeeper warnings (signed build only)
- [ ] App launches and the window opens within ~3 s
- [ ] Health indicator in the header turns green (sidecar spawned + DB migrated)
- [ ] Dashboard shows the default watchlist populated with assets
- [ ] Opening an asset page shows price history + a candlestick chart
- [ ] Settings page loads, can toggle a boolean setting, toggle persists across restart
- [ ] SQLite DB lands in the expected path:
      - macOS: `~/Library/Application Support/FinTrack/fintrack.db`
      - Windows: `%APPDATA%\FinTrack\fintrack.db`
- [ ] Quitting via ⌘Q / window close terminates the sidecar process within ~1 s
      (check Activity Monitor / Task Manager — no orphaned `fintrack-sidecar`)

---

## Updates (second release onward)

Once v0.1.0 is out:

- Bump versions (`tauri.conf.json` + `Cargo.toml` + `pyproject.toml`)
- Tag as `v0.1.1` / `v0.2.0` / etc. and push
- The release workflow produces a new `latest.json` (the updater feed)
  pointing at the new artefacts
- Clients running v0.1.0 will see the update on their next check

The updater feed URL is pinned in `tauri.conf.json`:

```
https://github.com/MasimbaTakudzwa/FinTrack/releases/latest/download/latest.json
```

GitHub's `latest/download/<file>` redirect always resolves to the most recent
non-draft release, so **don't promote a draft to published until you've
verified the build** — the moment you do, all existing installs start seeing
that release as "latest".

---

## Rolling back a bad release

If a release ships and users report it's broken:

1. **Delete the release** (or re-mark as draft): `gh release delete v0.1.1 --yes`
2. Clients that already auto-updated keep the broken version — the updater
   doesn't downgrade. Instructions to users: uninstall + download the previous
   release manually
3. Cut a fixed release as `v0.1.2` with priority — the updater will pull it
   on the next check

Prevention: promote draft → published only after the smoke checklist passes
on a clean machine.

---

## Troubleshooting

**Build job fails at "Verify frozen binary exists" step**
- PyInstaller couldn't find a hidden import. Check the workflow logs for
  `WARNING: Hidden import 'foo' not found`.
- Add to `hiddenimports` in `sidecar.spec` (or to the appropriate
  `collect_submodules` call) and re-run.

**Mac build fails with `security: SecKeychainItemImport: One or more parameters passed to a function were not valid`**
- Tauri's macOS bundler is "truthy == sign it": if `APPLE_CERTIFICATE` is
  exported as an empty string (which is what `${{ secrets.APPLE_CERTIFICATE }}`
  expands to when the secret is unset), Tauri will attempt `security import`
  with zero bytes and fail.
- The release workflow handles this by carrying secrets through as
  `_SEC_*`-prefixed env vars and only re-exporting the canonical
  `APPLE_CERTIFICATE` / `APPLE_*` / `WINDOWS_CERTIFICATE` / etc. when the
  source secret is non-empty. If you add new signing env vars, extend the
  same pattern in `.github/workflows/release.yml`.

**Local `pnpm tauri build` fails with `A public key has been found, but no private key`**
- Plain `tauri build` honours `bundle.createUpdaterArtifacts: true` from
  `tauri.conf.json` and demands `TAURI_SIGNING_PRIVATE_KEY` to sign the
  `.app.tar.gz` / `.msi.zip` updater bundles.
- For unsigned local bundles, use `pnpm -C shell tauri:build:unsigned`
  (override that disables updater artifacts).
- For signed local bundles, first generate the keypair (see "Updater
  keypair" above) and `export TAURI_SIGNING_PRIVATE_KEY="$(cat ~/.fintrack/fintrack.key)"`
  before `pnpm -C shell tauri build`.

**Build succeeds but app launches to a white window**
- Sidecar isn't starting. Run `./fintrack-sidecar` inside
  `FinTrack.app/Contents/Resources/_up_/_up_/dist/fintrack-sidecar/` directly
  and read the stderr.
- Common cause: a data file wasn't bundled (e.g. an Alembic migration). Add
  to `datas` in `sidecar.spec`.

**Mac build fails with "resource fork, Finder information, or similar detritus not allowed"**
- The frozen sidecar picked up xattr metadata. Add `xattr -cr dist/fintrack-sidecar/`
  to the workflow before `pnpm tauri build`.

**Notarisation hangs or times out**
- Check Apple's system status: [developer.apple.com/system-status](https://developer.apple.com/system-status/)
- `notarytool` sometimes takes 15+ minutes during high-traffic periods.
  Workflow default timeout is 30 min.

**Windows build succeeds but SmartScreen blocks the installer**
- Expected until you've built up "reputation" with Microsoft (thousands of
  installs typically), OR
- You're using a standard-validation cert rather than EV. EV certs get
  instant reputation. Azure Trusted Signing counts as EV.

**User reports "FinTrack is damaged and can't be opened, you should move it to the bin" on macOS**
- Apple Silicon Gatekeeper bite on an adhoc-signed (unsigned) build that carries
  the `com.apple.quarantine` attribute set by the browser at download time. The
  app is fine — Gatekeeper just refuses to run unsigned quarantined binaries.
- User fix: `xattr -cr /Applications/FinTrack.app`, then open normally.
- Permanent fix: ship signed + notarized builds. The `APPLE_CERTIFICATE` +
  notarization secrets are already wired in `release.yml`; populate them and
  the next release will launch clean on first download. $99/yr.
- Why the README's older "right-click → Open" advice doesn't work here: that
  workaround clears the *"unidentified developer"* dialog (Intel Macs / older
  macOS). Apple Silicon + recent macOS shows *"damaged"* for the same underlying
  condition and Finder doesn't surface an "Open Anyway" button for it.

---

## Related files

- `sidecar.spec` — PyInstaller config
- `requirements-packaging.txt` — PyInstaller pin
- `shell/src-tauri/tauri.conf.json` — Tauri bundle + updater config
- `shell/src-tauri/src/lib.rs` — `find_frozen_sidecar()` + updater plugin registration
- `.github/workflows/release.yml` — the pipeline
- `.claude/ARCHITECTURE.md` — packaging architecture (one-folder vs one-file, `_up_` encoding)
