import json
import unittest
from unittest import mock

import websocket

from odoo_print_client import core


class FakeSocket:
    """A websocket that yields scripted frames, then closes."""

    def __init__(self, frames):
        self.frames = list(frames)
        self.sent = []
        self.connected = True
        self.closed = False

    def send(self, data):
        self.sent.append(json.loads(data))

    def recv(self):
        if self.frames:
            return self.frames.pop(0)
        self.connected = False
        raise websocket.WebSocketConnectionClosedException()

    def ping(self):
        pass

    def close(self):
        self.closed = True


def _session_mock():
    session = mock.MagicMock()
    session.__enter__.return_value = session
    session.post.return_value.raise_for_status.return_value = None
    session.cookies.get.return_value = "sid"
    return session


JOB = {"printer_name": "LBL11", "file_data": "XlhBXlha", "file_type": "qweb-text"}


class ResumeFromLastNotificationTest(unittest.TestCase):
    def _connect(self, frames, state):
        sock = FakeSocket(frames)
        with mock.patch.object(core.requests, "Session", return_value=_session_mock()), \
                mock.patch.object(core.websocket, "create_connection", return_value=sock), \
                mock.patch.object(core, "process_print_job") as printed:
            with self.assertRaises(websocket.WebSocketConnectionClosedException):
                core._connect_and_listen(
                    "https://odoo", "prod", "u", "p", "LBL11", state=state
                )
        return sock, printed

    def test_first_connection_subscribes_from_zero_and_tracks_ids(self):
        state = {"last": 0}
        frames = [json.dumps([
            {"id": 5, "message": {"type": "bus.bus/im_status_updated", "payload": {}}},
            {"id": 7, "message": {"type": "print_job", "payload": JOB}},
        ])]
        sock, printed = self._connect(frames, state)
        self.assertEqual(sock.sent[0]["data"], {"channels": ["LBL11"], "last": 0})
        printed.assert_called_once_with(JOB)
        self.assertEqual(state["last"], 7)
        self.assertTrue(sock.closed)

    def test_reconnection_resumes_after_the_last_id(self):
        state = {"last": 7}
        frames = [json.dumps([
            {"id": 9, "message": {"type": "print_job", "payload": JOB}},
            {"id": 3, "message": {"type": "print_job", "payload": JOB}},
        ])]
        sock, printed = self._connect(frames, state)
        self.assertEqual(sock.sent[0]["data"]["last"], 7)
        self.assertEqual(printed.call_count, 2)
        self.assertEqual(state["last"], 9, "a lower id never moves the cursor back")

    def test_malformed_frames_do_not_break_the_cursor(self):
        state = {"last": 0}
        frames = ["not json", json.dumps({"not": "a list"}), json.dumps([42, {"id": "x"}, {"id": 2, "message": {}}])]
        sock, printed = self._connect(frames, state)
        printed.assert_not_called()
        self.assertEqual(state["last"], 2)

    def test_run_client_keeps_the_cursor_across_connections(self):
        seen = []

        def fake_connect(*args, **kwargs):
            state = kwargs["state"]
            seen.append(state["last"])
            if len(seen) == 1:
                kwargs["on_connected"]()
                state["last"] = 42
                raise websocket.WebSocketConnectionClosedException()
            raise KeyboardInterrupt()

        with mock.patch.object(core, "_connect_and_listen", side_effect=fake_connect), \
                mock.patch.object(core.time, "sleep") as sleep:
            core.run_client("https://odoo", "prod", "u", "p", "LBL11")
        self.assertEqual(seen, [0, 42])
        sleep.assert_called_once_with(core._RETRY_DELAYS[0])


if __name__ == "__main__":
    unittest.main()
