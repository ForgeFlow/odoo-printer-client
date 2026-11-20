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
    session.post.return_value.json.return_value = {"result": {"uid": 7}}
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
        printed.assert_called_once_with(JOB, None)
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
                mock.patch.object(core.JobTracker, "start"), \
                mock.patch.object(core.time, "sleep") as sleep:
            core.run_client("https://odoo", "prod", "u", "p", "LBL11")
        self.assertEqual(seen, [0, 42])
        sleep.assert_called_once_with(core._RETRY_DELAYS[0])



def _run(stdout=b""):
    return mock.MagicMock(stdout=stdout)


def _rpc(result=True, error=None):
    response = mock.MagicMock()
    response.json.return_value = {"error": error} if error else {"result": result}
    return response


class JobTrackerTest(unittest.TestCase):
    def setUp(self):
        self.tracker = core.JobTracker("https://odoo", "prod", "u", "p")
        self.session = mock.MagicMock()
        self.session.post.return_value = _rpc()
        self.tracker.session = self.session

    def _poll(self, queue):
        def run(cmd, **kwargs):
            self.assertEqual(cmd[:3], ["lpstat", "-W", "not-completed"])
            return _run(queue)

        with mock.patch.object(core.subprocess, "run", side_effect=run):
            self.tracker.poll()

    def _reported(self):
        return [
            (call.args[0], call.kwargs["json"]["params"])
            for call in self.session.post.call_args_list
        ]

    def test_reports_the_job_when_it_leaves_the_queue(self):
        def lp(cmd, **kwargs):
            self.assertEqual(cmd[0], "lp")
            self.assertEqual(kwargs["env"]["LC_ALL"], "C")
            return _run(b"request id is LBL11-42 (1 file(s))\n")

        with mock.patch.object(core.subprocess, "run", side_effect=lp):
            core.process_print_job(dict(JOB, job_uuid="abc"), self.tracker)
        self._poll(b"LBL11-42 pi 1024 Thu 20 Nov 2026\n")
        self.assertEqual(self._reported(), [])
        self._poll(b"")
        self.assertEqual(self._reported(), [(
            "https://odoo/printing/websocket/job/state",
            {"job_uuid": "abc", "state": "completed", "cups_job_id": 42},
        )])
        self._poll(b"")
        self.assertEqual(len(self._reported()), 1)

    def test_refused_job_is_reported_aborted_even_without_lpstat(self):
        error = core.subprocess.CalledProcessError(1, "lp")
        with mock.patch.object(core.subprocess, "run", side_effect=error):
            core.process_print_job(dict(JOB, job_uuid="abc"), self.tracker)
        self.tracker.add("def", "LBL11-43")
        failing = core.subprocess.TimeoutExpired("lpstat", 10)
        with mock.patch.object(core.subprocess, "run", side_effect=failing):
            with self.assertRaises(core.subprocess.TimeoutExpired):
                self.tracker.poll()
        self.assertEqual(self._reported()[0][1], {"job_uuid": "abc", "state": "aborted"})

    def test_server_error_is_retried_and_unknown_job_dropped(self):
        self.tracker.add("abc", "LBL11-42")
        self.tracker.add("gone", "LBL11-44")
        self.session.post.side_effect = [_rpc(error={"code": 200, "message": "boom"})]
        with self.assertRaises(RuntimeError):
            self._poll(b"")
        self.session.post.side_effect = [_rpc(), _rpc(error={"code": 404})]
        self._poll(b"")
        self.assertEqual(self.tracker.jobs, {})

    def test_expired_session_is_renewed_in_the_same_report(self):
        self.tracker.add("abc", "LBL11-42")
        self.session.post.return_value = _rpc(error={"code": 100})
        fresh = _session_mock()
        fresh.post.side_effect = [_rpc({"uid": 7}), _rpc()]
        with mock.patch.object(core.requests, "Session", return_value=fresh):
            self._poll(b"")
        self.assertEqual(self.tracker.jobs, {})
        self.assertEqual(
            fresh.post.call_args_list[1].args[0], "https://odoo/printing/websocket/job/state"
        )

    def test_job_without_uuid_is_not_tracked(self):
        with mock.patch.object(
            core.subprocess, "run", return_value=_run(b"request id is LBL11-43 (1 file(s))\n")
        ):
            core.process_print_job(JOB, self.tracker)
        self.assertEqual(self.tracker.jobs, {})


if __name__ == "__main__":
    unittest.main()
