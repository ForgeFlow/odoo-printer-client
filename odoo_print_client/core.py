import base64
import json
import logging
import os
import re
import subprocess
import threading
import time
import requests
import websocket
import ssl

_logger = logging.getLogger(__name__)


def process_print_job(payload_dict, tracker=None):
    """
    Validates the payload and pipes the file bytes directly from memory
    into the CUPS spooler via stdin, ensuring zero footprint on disk.
    Supports PDF and ZPL (.txt) file types.
    """
    cups_job = _print_payload(payload_dict)
    if tracker and payload_dict.get("job_uuid"):
        tracker.add(payload_dict["job_uuid"], cups_job)


def _print_payload(payload_dict):
    """Return the CUPS job id, or None when the job is refused."""
    _logger.info("New print job received")
    printer_name = payload_dict.get("printer_name", "")
    file_data_b64 = payload_dict.get("file_data", "")
    file_type = payload_dict.get("file_type", "pdf").lower()

    if not file_data_b64:
        _logger.error("Incomplete payload. Missing file data.")
        return None

    try:
        file_bytes = base64.b64decode(file_data_b64, validate=True)
    except Exception:
        _logger.error("Security Error: Content is not valid Base64.")
        return None

    if file_type in ["qweb-text", "raw"]:
        return _print_zpl(file_bytes, printer_name)
    elif file_type in ["qweb-pdf", "pdf"]:
        return _print_pdf(file_bytes, printer_name)
    _logger.error("SECURITY ALERT: Unsupported file type '%s'. Aborting.", file_type)
    return None


def _lp(cmd, data):
    """Queue the data in CUPS and return the CUPS job id ("Printer-12")."""
    result = subprocess.run(
        cmd, input=data, check=True, capture_output=True, timeout=LP_TIMEOUT,
        env={**os.environ, "LC_ALL": "C"},
    )
    match = re.search(r"request id is (\S+-\d+)", result.stdout.decode(errors="replace"))
    if not match:
        raise RuntimeError("lp did not return a job id")
    return match.group(1)


def _print_pdf(pdf_bytes, printer_name):
    if not pdf_bytes.startswith(b"%PDF"):
        _logger.error("SECURITY ALERT: The sent file is NOT a real PDF. Aborting.")
        return None

    target = printer_name or "Default"
    _logger.info("Sending PDF from RAM to printer: %s", target)
    cmd = ["lp"]
    if printer_name:
        cmd.extend(["-d", printer_name])

    try:
        _logger.debug("Executing command: %s", " ".join(cmd))
        cups_job = _lp(cmd, pdf_bytes)
        _logger.info("PDF print job %s successfully queued in CUPS (zero disk footprint)", cups_job)
        return cups_job
    except subprocess.CalledProcessError as e:
        _logger.error("CUPS error during printing: %s", e)
    except FileNotFoundError:
        _logger.error("'lp' command not found. Is CUPS installed?")
    except Exception as e:
        _logger.exception("Unexpected error during PDF printing: %s", e)
    return None


def _print_zpl(zpl_bytes, printer_name):
    if not zpl_bytes.lstrip().startswith(b"^XA"):
        _logger.error("SECURITY ALERT: The sent file does not look like valid ZPL (missing ^XA). Aborting.")
        return None

    target = printer_name or "Default"
    _logger.info("Sending ZPL from RAM to printer: %s (raw mode)", target)
    cmd = ["lp", "-o", "raw"]
    if printer_name:
        cmd.extend(["-d", printer_name])

    try:
        _logger.debug("Executing command: %s", " ".join(cmd))
        cups_job = _lp(cmd, zpl_bytes)
        _logger.info("ZPL print job %s successfully queued in CUPS (zero disk footprint)", cups_job)
        return cups_job
    except subprocess.CalledProcessError as e:
        _logger.error("CUPS error during printing: %s", e)
    except FileNotFoundError:
        _logger.error("'lp' command not found. Is CUPS installed?")
    except Exception as e:
        _logger.exception("Unexpected error during ZPL printing: %s", e)
    return None


_RETRY_DELAYS = [5, 10, 30, 60, 120]
DEFAULT_PING_INTERVAL = 30
DEFAULT_POLL_INTERVAL = 5
HTTP_TIMEOUT = 30
LP_TIMEOUT = 60
LPSTAT_TIMEOUT = 10


def _authenticate(url, db, user, password, ssl_verify=True):
    session = requests.Session()
    session.verify = ssl_verify
    resp = session.post(f"{url}/web/session/authenticate", json={
        "jsonrpc": "2.0",
        "method": "call",
        "params": {"db": db, "login": user, "password": password},
    }, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    if resp.json().get("error") or not session.cookies.get("session_id"):
        raise RuntimeError("Could not obtain session_id. Check credentials.")
    return session


def _queued_cups_jobs():
    """The ids of the jobs still in the CUPS queue, of every printer."""
    result = subprocess.run(
        ["lpstat", "-W", "not-completed", "-o"],
        check=True, capture_output=True, timeout=LPSTAT_TIMEOUT,
        env={**os.environ, "LC_ALL": "C"},
    )
    return {
        line.split()[0]
        for line in result.stdout.decode(errors="replace").splitlines()
        if line.strip()
    }


class JobTracker:
    """Tells Odoo when the jobs it sent leave the CUPS queue."""

    def __init__(self, url, db, user, password, ssl_verify=True,
                 poll_interval=DEFAULT_POLL_INTERVAL):
        self.url = url
        self.db = db
        self.user = user
        self.password = password
        self.ssl_verify = ssl_verify
        self.poll_interval = max(1, poll_interval)
        self.session = None
        self.jobs = {}
        self.lock = threading.Lock()
        self.failing = False

    def add(self, job_uuid, cups_job):
        """Track a job; a job CUPS refused (cups_job None) is aborted."""
        with self.lock:
            self.jobs[job_uuid] = {
                "cups_job": cups_job,
                "state": None if cups_job else "aborted",
            }

    def start(self):
        thread = threading.Thread(target=self._run, name="job-tracker", daemon=True)
        thread.start()
        return thread

    def _run(self):
        while True:
            try:
                self.poll()
                if self.failing:
                    _logger.info("Reporting print job states to Odoo again")
                    self.failing = False
            except Exception as e:
                if not self.failing:
                    _logger.error("Cannot report print job states to Odoo: %s", e)
                    self.failing = True
            time.sleep(self.poll_interval)

    def poll(self):
        self._send_finished()
        with self.lock:
            waiting = any(job["state"] is None for job in self.jobs.values())
        if not waiting:
            return
        queued = _queued_cups_jobs()
        with self.lock:
            for job in self.jobs.values():
                if job["state"] is None and job["cups_job"] not in queued:
                    _logger.info("Print job %s left the CUPS queue", job["cups_job"])
                    job["state"] = "completed"
        self._send_finished()

    def _send_finished(self):
        with self.lock:
            finished = [(uuid, dict(job)) for uuid, job in self.jobs.items() if job["state"]]
        for job_uuid, job in finished:
            self._send(job_uuid, job)
            with self.lock:
                self.jobs.pop(job_uuid, None)

    def _send(self, job_uuid, job):
        """Report the state of a job. Odoo not knowing the job drops the
        report; any other failure raises, so the report is sent again."""
        params = {"job_uuid": job_uuid, "state": job["state"]}
        if job["cups_job"]:
            params["cups_job_id"] = int(job["cups_job"].rsplit("-", 1)[1])
        for _attempt in range(2):
            if self.session is None:
                self.session = _authenticate(
                    self.url, self.db, self.user, self.password, self.ssl_verify
                )
            resp = self.session.post(f"{self.url}/printing/websocket/job/state", json={
                "jsonrpc": "2.0",
                "method": "call",
                "params": params,
            }, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            error = resp.json().get("error")
            if not error:
                return
            if error.get("code") == 404:
                _logger.error("Odoo does not know print job %s, its state is dropped", job_uuid)
                return
            if error.get("code") != 100:
                break
            self.session = None
        raise RuntimeError(f"Odoo refused the state of print job {job_uuid}: {error.get('message')}")


def run_client(url, db, user, password, channel, ssl_verify=True, ping_interval=DEFAULT_PING_INTERVAL,
               poll_interval=DEFAULT_POLL_INTERVAL):
    """
    Authenticates with Odoo and starts the WebSocket listening loop.
    Reconnects automatically on failure with exponential backoff. The backoff
    is reset every time a connection is successfully established, so a healthy
    client that gets disconnected reconnects quickly instead of waiting for the
    longest delay. Every reconnection subscribes from the last bus notification
    id received, so the jobs Odoo sent while the client was disconnected are
    delivered on reconnection instead of being lost (a subscription from 0 only
    replays the last 50 seconds of the bus).
    """
    attempt = 0
    state = {"last": 0}
    tracker = JobTracker(url, db, user, password, ssl_verify=ssl_verify,
                         poll_interval=poll_interval)
    tracker.start()
    while True:
        connected = False

        def _on_connected():
            nonlocal connected
            connected = True

        try:
            _connect_and_listen(
                url, db, user, password, channel,
                ssl_verify=ssl_verify,
                ping_interval=ping_interval,
                on_connected=_on_connected,
                state=state,
                tracker=tracker,
            )
            _logger.info("Connection closed by server.")
        except KeyboardInterrupt:
            _logger.info("Shutting down.")
            break
        except Exception as e:
            _logger.error("Unexpected error: %s", e)

        if connected:
            attempt = 0
        delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
        _logger.info("Reconnecting in %d seconds... (attempt %d)", delay, attempt + 1)
        time.sleep(delay)
        attempt += 1


def _connect_and_listen(url, db, user, password, channel, ssl_verify=True,
                        ping_interval=DEFAULT_PING_INTERVAL, on_connected=None,
                        state=None, tracker=None):
    """
    One connection: authenticate, subscribe to the channel from the last
    notification id in `state` and dispatch print jobs until the socket
    closes. `state["last"]` is updated with every notification id received, so
    the caller can resume from it on the next connection.
    """
    if state is None:
        state = {"last": 0}
    _logger.info("Authenticating with Odoo at %s (DB: %s)...", url, db)
    with _authenticate(url, db, user, password, ssl_verify) as session:
        session_id = session.cookies.get("session_id")

    ws_url = url.replace("http", "ws").replace("https", "wss") + "/websocket"
    _logger.info("Connecting to WebSocket at %s...", ws_url)

    socket_sslopt = {"cert_reqs": ssl.CERT_NONE} if not ssl_verify else {}
    socket = websocket.create_connection(
        ws_url,
        cookie=f"session_id={session_id}",
        sslopt=socket_sslopt,
        timeout=ping_interval,
    )
    try:
        socket.send(json.dumps({
            'event_name': 'subscribe',
            'data': {'channels': [channel], 'last': state["last"]}
        }))

        if state["last"]:
            _logger.info("Connected and listening on '%s' channel, resuming after notification %d...", channel, state["last"])
        else:
            _logger.info("Connected and listening on '%s' channel...", channel)
        if on_connected:
            on_connected()

        while True:
            try:
                raw_message = socket.recv()
            except websocket.WebSocketTimeoutException:
                _logger.debug("No traffic for %ds, sending ping", ping_interval)
                socket.ping()
                continue
            if not raw_message:
                if not socket.connected:
                    return
                continue
            try:
                data = json.loads(raw_message)
            except json.JSONDecodeError:
                continue

            if isinstance(data, list):
                for event in data:
                    if not isinstance(event, dict):
                        continue
                    event_id = event.get("id")
                    if isinstance(event_id, int) and event_id > state["last"]:
                        state["last"] = event_id
                    bus_message = event.get("message", {})
                    if isinstance(bus_message, dict) and bus_message.get("type") == "print_job":
                        real_payload = bus_message.get("payload", {})
                        if "file_data" in real_payload:
                            process_print_job(real_payload, tracker)
    finally:
        socket.close()
