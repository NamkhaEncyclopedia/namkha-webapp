"""Browser E2E fixtures.

The whole directory is skipped cleanly unless the opt-in `browser` poetry group is
installed (`poetry install --with browser` + `playwright install chromium`), so the
default backend suite runs without a browser.
"""

import contextlib
import socket
import threading
import time

import pytest

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
