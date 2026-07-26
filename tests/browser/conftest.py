"""Browser E2E fixtures.

The whole directory is skipped cleanly unless the opt-in `browser` poetry group is
installed (`poetry install --with browser` + `playwright install chromium`), so the
default backend suite runs without a browser.
"""

import contextlib
import os
import socket
import threading
import time

import pytest

# Cloudflare's always-passes test keys: the real widget only accepts the deployed
# host names, and app startup refuses to boot without a secret. setdefault, not
# monkeypatch -- the session-scoped server below starts before function-scoped
# fixtures run. The api.js request itself is stubbed per page (see _stub_turnstile),
# so the browser suite stays offline.
os.environ.setdefault("TURNSTILE_SITEKEY", "1x00000000000000000000AA")
os.environ.setdefault("TURNSTILE_SECRET", "1x0000000000000000000000000000000AA")
os.environ.setdefault("TURNSTILE_HOSTNAMES", "localhost,127.0.0.1")
os.environ.setdefault("NAMKHA_LOG_SALT", "test-salt")

try:
    import playwright.sync_api  # noqa: F401
    import pytest_playwright  # noqa: F401
except ImportError:
    # No Playwright -> don't collect any test in this directory.
    collect_ignore_glob = ["*"]
else:
    import uvicorn

    def _free_port():
        with contextlib.closing(socket.socket()) as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    class _ThreadedServer(uvicorn.Server):
        def install_signal_handlers(self):
            # Signal handlers can only be installed on the main thread.
            pass

    # Stand-in for Cloudflare's api.js: does what the real script does to the
    # page (define window.turnstile, add the hidden token input to the form)
    # without a network call or a real challenge.
    _TURNSTILE_STUB = """
    window.turnstile = {
      render() { return 'stub-widget'; },
      remove() {},
      reset() {
        document.querySelectorAll('input[name="cf-turnstile-response"]')
          .forEach((input) => { input.value = 'stub-token'; });
      },
    };
    document.querySelectorAll('.cf-turnstile').forEach((widget) => {
      const form = widget.closest('form');
      if (!form) return;
      const input = document.createElement('input');
      input.type = 'hidden';
      input.name = 'cf-turnstile-response';
      input.value = 'stub-token';
      form.appendChild(input);
    });
    """

    @pytest.fixture(autouse=True)
    def _stub_turnstile(page):
        page.route(
            "**/challenges.cloudflare.com/**",
            lambda route: route.fulfill(
                status=200,
                content_type="application/javascript",
                body=_TURNSTILE_STUB,
            ),
        )

    @pytest.fixture(scope="session")
    def live_server():
        """Run the real app on a background uvicorn thread; yield its base URL."""
        from app.main import app

        port = _free_port()
        server = _ThreadedServer(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.time() + 15
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        if not server.started:
            raise RuntimeError("uvicorn test server failed to start")
        yield f"http://127.0.0.1:{port}"
        server.should_exit = True
        thread.join(timeout=5)
