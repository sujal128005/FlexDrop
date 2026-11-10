"""One-line launcher for Google Colab / Jupyter.

    from flexdrop.colab import launch
    launch()

Starts FlexDrop Studio in a background thread and opens it in a new browser tab through
Colab's port proxy (or prints the local URL outside Colab).
"""

from __future__ import annotations

import threading
import time

import uvicorn

from flexdrop.studio import build_studio_app

_SERVERS: dict[int, uvicorn.Server] = {}


def launch(
    port: int = 8000, models: str = "models", reports: str = "reports", partners: int = 40, start: str = "auto"
) -> str:
    if port in _SERVERS and _SERVERS[port].started:
        url = f"http://127.0.0.1:{port}/app/"
        _open(port, url)
        return url
    app, _, _ = build_studio_app(model_dir=models, reports_dir=reports, n_partners=partners, start=start)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, name=f"flexdrop-studio-{port}", daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.1)
    if not server.started:
        raise RuntimeError(f"FlexDrop Studio did not start on port {port} (is the port already in use?)")
    _SERVERS[port] = server
    url = f"http://127.0.0.1:{port}/app/"
    _open(port, url)
    return url


def stop(port: int = 8000) -> None:
    server = _SERVERS.pop(port, None)
    if server is not None:
        server.should_exit = True


def _open(port: int, url: str) -> None:
    try:
        from google.colab import output

        output.serve_kernel_port_as_window(port, path="/app/")
        print("FlexDrop Studio is running - click the link above to open it in a new tab.")
    except ImportError:
        print(f"FlexDrop Studio is running at {url}")
