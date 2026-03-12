import base64
import json
import logging
import subprocess
import time
import requests
import websocket

_logger = logging.getLogger(__name__)


def process_print_job(payload_dict):
    """
    Validates the payload and pipes the file bytes directly from memory
    into the CUPS spooler via stdin, ensuring zero footprint on disk.
    Supports PDF and ZPL (.txt) file types.
    """
    _logger.info("New print job received")
    printer_name = payload_dict.get("printer_name", "")
    file_data_b64 = payload_dict.get("file_data", "")
    file_type = payload_dict.get("file_type", "pdf").lower()

    if not file_data_b64:
        _logger.error("Incomplete payload. Missing file data.")
        return

    try:
        file_bytes = base64.b64decode(file_data_b64, validate=True)
    except Exception:
        _logger.error("Security Error: Content is not valid Base64.")
        return

    if file_type in ["qweb-text", "raw"]:
        _print_zpl(file_bytes, printer_name)
    elif file_type in ["qweb-pdf", "pdf"]:
        _print_pdf(file_bytes, printer_name)
    else:
        _logger.error("SECURITY ALERT: Unsupported file type '%s'. Aborting.", file_type)


def _print_pdf(pdf_bytes, printer_name):
    if not pdf_bytes.startswith(b"%PDF"):
        _logger.error("SECURITY ALERT: The sent file is NOT a real PDF. Aborting.")
        return

    target = printer_name or "Default"
    _logger.info("Sending PDF from RAM to printer: %s", target)
    cmd = ["lp"]
    if printer_name:
        cmd.extend(["-d", printer_name])

    try:
        _logger.debug("Executing command: %s", " ".join(cmd))
        subprocess.run(cmd, input=pdf_bytes, check=True)
        _logger.info("PDF print job successfully queued in CUPS (zero disk footprint)")
    except subprocess.CalledProcessError as e:
        _logger.error("CUPS error during printing: %s", e)
    except FileNotFoundError:
        _logger.error("'lp' command not found. Is CUPS installed?")
    except Exception as e:
        _logger.exception("Unexpected error during PDF printing: %s", e)


def _print_zpl(zpl_bytes, printer_name):
    if not zpl_bytes.lstrip().startswith(b"^XA"):
        _logger.error("SECURITY ALERT: The sent file does not look like valid ZPL (missing ^XA). Aborting.")
        return

    target = printer_name or "Default"
    _logger.info("Sending ZPL from RAM to printer: %s (raw mode)", target)
    cmd = ["lp", "-o", "raw"]
    if printer_name:
        cmd.extend(["-d", printer_name])

    try:
        _logger.debug("Executing command: %s", " ".join(cmd))
        subprocess.run(cmd, input=zpl_bytes, check=True)
        _logger.info("ZPL print job successfully queued in CUPS (zero disk footprint)")
    except subprocess.CalledProcessError as e:
        _logger.error("CUPS error during printing: %s", e)
    except FileNotFoundError:
        _logger.error("'lp' command not found. Is CUPS installed?")
    except Exception as e:
        _logger.exception("Unexpected error during ZPL printing: %s", e)


_RETRY_DELAYS = [5, 10, 30, 60, 120]


def run_client(url, db, user, password, channel):
    """
    Authenticates with Odoo and starts the WebSocket listening loop.
    Reconnects automatically on failure with exponential backoff.
    """
    attempt = 0
    while True:
        try:
            _connect_and_listen(url, db, user, password, channel)
        except KeyboardInterrupt:
            _logger.info("Shutting down.")
            break
        except Exception as e:
            _logger.error("Unexpected error: %s", e)

        delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
        _logger.info("Reconnecting in %d seconds... (attempt %d)", delay, attempt + 1)
        time.sleep(delay)
        attempt += 1


def _connect_and_listen(url, db, user, password, channel):
    auth_payload = {
        "jsonrpc": "2.0",
        "method": "call",
        "params": {"db": db, "login": user, "password": password}
    }

    _logger.info("Authenticating with Odoo at %s (DB: %s)...", url, db)
    with requests.Session() as session:
        resp = session.post(f"{url}/web/session/authenticate", json=auth_payload)
        resp.raise_for_status()
        session_id = session.cookies.get("session_id")

    if not session_id:
        raise RuntimeError("Could not obtain session_id. Check credentials.")

    ws_url = url.replace("http", "ws").replace("https", "wss") + "/websocket"
    _logger.info("Connecting to WebSocket at %s...", ws_url)

    socket = websocket.create_connection(ws_url, cookie=f"session_id={session_id}")
    socket.send(json.dumps({
        'event_name': 'subscribe',
        'data': {'channels': [channel], 'last': 0}
    }))

    _logger.info("Connected and listening on '%s' channel...", channel)

    while True:
        raw_message = socket.recv()
        try:
            data = json.loads(raw_message)
        except json.JSONDecodeError:
            continue

        if isinstance(data, list):
            for event in data:
                bus_message = event.get("message", {})
                if isinstance(bus_message, dict) and bus_message.get("type") == "print_job":
                    real_payload = bus_message.get("payload", {})
                    if "file_data" in real_payload:
                        process_print_job(real_payload)
