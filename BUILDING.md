# Build and release

Use 64-bit Python 3.11 or newer. Install `requirements.txt`, then run `python app.py`. On macOS, `bash scripts/setup-mac.sh` also builds the pinned native DDC helper. `--demo` uses simulated monitors without hardware writes.

Install `requirements-build.txt` and run `python scripts/build.py` on the target OS. Run `python scripts/smoke_package.py` to test the frozen worker without touching hardware. Run `python -m unittest discover -s tests -q` for the test suite.

Set the version in `monitor_switch/version.py`, update the one-line `RELEASE_NOTES.md`, and push a matching `vX.Y.Z` tag. GitHub Actions builds Apple Silicon and Windows x64 ZIPs and publishes signed release metadata. The repository secret `UPDATE_SIGNING_KEY` holds the base64-encoded 32-byte Ed25519 private key. Its matching public key is pinned in the app. Never commit the private key. A fork needs its own repository identity and key pair.

Automatic updates apply to installed packaged apps. An existing Python installation needs one manual launch of the packaged Windows release to migrate. Keep the ZIP's complete `app` directory. Quit the previous tray app first, and allow the new executable through Windows Firewall on the intended private/Tailscale network. Later updates keep the executable path and user settings.
