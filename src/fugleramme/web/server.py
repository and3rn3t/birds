"""HTTP server for the kiosk and admin views.

The kiosk (`/`) serves one thing: a full-color page of what the frame is
showing, full-screen. It never reloads; instead it polls `/state` and swaps the
image only when the page actually changed, so a new bird appears within one poll
interval with no flash. Presentation settings live at `/admin` and are read per
request from the shared SettingsStore, so a change takes effect without a
restart. The kiosk is always open; the admin can be given a password (#52).

Routing and transport only - the admin page itself is built in admin.py, and the
files under static/ are served as they are.

Stdlib http.server only, but threaded with a socket timeout: a serial server is
one silent client away from a dead kiosk, since a connection that never sends a
request blocks the accept loop forever. It shares the render loop's detector
source, so the two halves of a page cost one round trip between them.

A request that cannot reach the detector answers 503 rather than an empty page,
and the kiosk keeps the picture it is holding.
"""

from __future__ import annotations

import base64
import binascii
import functools
import hashlib
import hmac
import io
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from urllib.parse import parse_qs, urlparse

from .. import __version__, auth, modes, updates
from ..config import DOCS_URL
from ..languages import namer
from ..panel import Panel, resolution_of
from ..picks import Picks
from ..render.paper import paper_tile
from ..settings import Settings, SettingsStore, merged
from ..source import Source, Unavailable
from ..status import Status
from . import STATIC_DIR, admin

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15

HTML = "text/html; charset=utf-8"
JSON = "application/json"

# Served verbatim. The admin's link carries ?v=<version>, so an update busts the cache.
FILES = {
    "/": ("kiosk.html", HTML),
    "/index.html": ("kiosk.html", HTML),
    "/admin.css": ("admin.css", "text/css; charset=utf-8"),
    "/admin.js": ("admin.js", "text/javascript; charset=utf-8"),
}


# What an admin password covers. Named rather than inferred, so a route added
# later is public only if someone put it on the open side on purpose. The kiosk
# and its poll stay out: a frame with a password still shows birds, and an
# uptime probe on /health still answers.
PROTECTED = frozenset({"/admin", "/preview.png", "/species", "/detector", "/update"})

REALM = "Fugleramme"


def credentials(header: str) -> tuple[str, str] | None:
    """The user and password out of an `Authorization: Basic` header."""
    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic":
        return None
    try:
        user, sep, password = base64.b64decode(encoded, validate=True).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return None
    return (user, password) if sep else None


@functools.cache
def paper_png() -> bytes:
    buffer = io.BytesIO()
    paper_tile().save(buffer, format="PNG")
    return buffer.getvalue()


def make_handler(
    source: Source,
    images_dir: Path,
    store: SettingsStore,
    picks: Picks,
    panel: Panel | None,
    status: Status,
):
    # Held across requests: an outage must not blank every viewer at once. Tied
    # to the detector that drew it, since another station's birds are not ours.
    last_page: bytes | None = None
    last_from = ""
    # The kiosk polls every few seconds, so one warning per failed request would
    # never stop. Say it once, and again on recovery.
    unreachable = False

    def note(message: str, gone: bool) -> None:
        nonlocal unreachable
        if gone and not unreachable:
            log.warning("%s", message)
        elif not gone and unreachable:
            log.info("Detector reachable again")
        else:
            log.debug("%s", message)
        unreachable = gone

    class Handler(BaseHTTPRequestHandler):
        timeout = REQUEST_TIMEOUT
        head = False
        # Set when a route answered from a held copy rather than the detector, so
        # a served page is not mistaken for the detector having come back.
        degraded = False

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        def log_error(self, fmt, *args):
            log.warning("%s %s", self.address_string(), fmt % args)

        def _send(self, status: int, body: bytes, content_type: str):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self._body(body)

        def _send_cached(self, body: bytes, content_type: str):
            # no-cache + ETag lets an unchanged refresh return a bodyless 304.
            etag = f'"{hashlib.md5(body).hexdigest()}"'
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("ETag", etag)
            self.end_headers()
            self._body(body)

        def _body(self, body: bytes):
            if not self.head:
                self.wfile.write(body)

        def _authorized(self, route: str) -> bool:
            settings = store.get()
            if route not in PROTECTED or not settings.admin_password_hash:
                return True
            given = credentials(self.headers.get("Authorization", ""))
            if given is None:
                return False
            return hmac.compare_digest(given[0], settings.admin_username) and auth.verify(
                settings.admin_password_hash, given[1]
            )

        def _unauthorized(self):
            self.send_response(401)
            self.send_header("WWW-Authenticate", f'Basic realm="{REALM}"')
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def _query(self) -> dict[str, list[str]]:
            return parse_qs(urlparse(self.path).query, keep_blank_values=True)

        def _context(self, settings: Settings) -> modes.Context:
            return modes.context(
                source,
                images_dir,
                picks,
                settings,
                namer(settings.primary_language, settings.secondary_language, store.path.parent),
                settings.web_size(resolution_of(panel)),
            )

        def _edited(self) -> Settings:
            """Saved settings under the admin's unsaved form state, so the
            preview and its listing show a change before Save."""
            return merged(store.get(), **admin.form_changes(self._query()))

        def _page_png(self):
            nonlocal last_page, last_from
            if source.base_url != last_from:
                last_page, last_from = None, source.base_url
            try:
                last_page = modes.png_bytes(self._context(store.get()))
            except Unavailable as exc:
                if last_page is None:
                    raise
                self.degraded = True
                note(f"Detector unavailable, serving the last kiosk page ({exc})", True)
            self._send_cached(last_page, "image/png")

        def _preview_png(self):
            self._send_cached(modes.png_bytes(self._context(self._edited())), "image/png")

        def _state(self):
            # Cheap enough to poll: one grouped query, no render.
            settings = store.get()
            token = modes.token(modes.state_key(self._context(settings)))
            # The kiosk's chrome links out to the detector, and the address can
            # change under it, so it rides the poll rather than page load.
            birdnet_url, birdnet_port = admin.birdnet_link(settings.detector_url)
            self._send(
                200,
                json.dumps(
                    {
                        "token": token,
                        "birdnetUrl": birdnet_url,
                        "birdnetPort": birdnet_port,
                        "docsUrl": DOCS_URL,
                    }
                ).encode(),
                JSON,
            )

        def _species(self):
            ctx = self._context(self._edited())
            rows = admin.subjects(ctx)
            self._send(
                200,
                json.dumps(
                    {"count": len(rows), "html": admin.species_html(rows, ctx.namer)}
                ).encode(),
                JSON,
            )

        def _admin(self):
            settings = store.get()
            html = admin.page(
                self._context(settings),
                settings,
                status,
                resolution_of(panel),
                panel is not None,
                store.path.parent,
            )
            self._send(200, html.encode(), HTML)

        def _update(self):
            self._send(
                200,
                json.dumps(
                    {
                        "updating": bool(status.updating or status.update_requested),
                        "version": __version__,
                        "phase": status.update_phase,
                        "percent": status.update_percent,
                    }
                ).encode(),
                JSON,
            )

        def _paper(self):
            self._send_cached(paper_png(), "image/png")

        def _health(self):
            self._send(200, b"ok", "text/plain")

        ROUTES: ClassVar[dict] = {
            "/collage.png": _page_png,
            "/preview.png": _preview_png,
            "/state": _state,
            "/paper.png": _paper,
            "/species": _species,
            "/admin": _admin,
            "/update": _update,
            "/health": _health,
        }

        def do_GET(self):
            route = urlparse(self.path).path
            if not self._authorized(route):
                self._unauthorized()
            elif route in FILES:
                name, content_type = FILES[route]
                self._send_cached((STATIC_DIR / name).read_bytes(), content_type)
            elif route in self.ROUTES:
                try:
                    self.ROUTES[route](self)
                    note("", self.degraded)
                except Unavailable as exc:
                    note(f"{route}: {exc}", True)
                    self._send(503, f"detector unavailable: {exc}".encode(), "text/plain")
            else:
                self._send(404, b"not found", "text/plain")

        def do_HEAD(self):
            # Full GET, body dropped: a wrong Content-Length is worse than no HEAD.
            self.head = True
            self.do_GET()

        def do_POST(self):
            route = urlparse(self.path).path
            length = int(self.headers.get("Content-Length", 0))
            # keep_blank_values: an emptied field is a change, not an absent one.
            # "None" for the second language and a cleared credential both post blank.
            form = parse_qs(self.rfile.read(length).decode(), keep_blank_values=True)
            # After the body: a 401 over an unread request body strands the client.
            if not self._authorized(route):
                self._unauthorized()
                return
            # POST, not a query: the connection test carries a password.
            if route == "/detector":
                answer = admin.connection(form, store.get())
                self._send(200, json.dumps(answer).encode(), JSON)
                return
            if route != "/admin":
                self._send(404, b"not found", "text/plain")
                return
            action = form.get("action", [""])[0]
            if action == "check":
                status.update_error = None
                status.update_available = updates.available(force=True)
            elif action == "update" and status.update_available and not updates.in_container():
                # The loop installs it: exiting mid-render or mid-push is not safe here.
                status.update_requested = status.update_available
            else:
                store.update(**admin.form_changes(form))
            self.send_response(303)
            self.send_header("Location", "/admin")
            self.end_headers()

    return Handler


def serve(
    source: Source,
    images_dir: Path,
    host: str,
    port: int,
    store: SettingsStore,
    picks: Picks,
    panel: Panel | None = None,
    status: Status | None = None,
) -> ThreadingHTTPServer:
    """Start the kiosk on a daemon thread. Port 0 asks the OS for one - read it
    back from `server_address[1]`."""
    handler = make_handler(source, images_dir, store, picks, panel, status or Status())
    httpd = ThreadingHTTPServer((host, port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
