import threading
import time
import unittest

from server_modules.session_manager.actor_queue import SessionActorQueue


class SessionActorQueueTests(unittest.TestCase):
    def test_same_session_turns_serialize(self):
        queue = SessionActorQueue()
        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()
        order = []

        def first() -> None:
            def op() -> None:
                first_entered.set()
                release_first.wait(timeout=1.0)
                order.append("first")

            queue.run("session-1", op)

        def second() -> None:
            first_entered.wait(timeout=1.0)

            def op() -> None:
                second_entered.set()
                order.append("second")

            queue.run("session-1", op)

        first_thread = threading.Thread(target=first)
        second_thread = threading.Thread(target=second)
        first_thread.start()
        second_thread.start()

        first_entered.wait(timeout=1.0)
        time.sleep(0.05)
        self.assertFalse(second_entered.is_set())

        release_first.set()
        first_thread.join(timeout=1.0)
        second_thread.join(timeout=1.0)

        self.assertEqual(order, ["first", "second"])

    def test_different_sessions_run_concurrently(self):
        queue = SessionActorQueue()
        release = threading.Event()
        lock = threading.Lock()
        running = 0
        max_running = 0
        both_started = threading.Event()

        def make_worker(actor_key: str):
            def worker() -> None:
                def op() -> None:
                    nonlocal running, max_running
                    with lock:
                        running += 1
                        max_running = max(max_running, running)
                        if running >= 2:
                            both_started.set()
                    release.wait(timeout=1.0)
                    with lock:
                        running -= 1

                queue.run(actor_key, op)

            return worker

        first_thread = threading.Thread(target=make_worker("session-1"))
        second_thread = threading.Thread(target=make_worker("session-2"))
        first_thread.start()
        second_thread.start()

        self.assertTrue(both_started.wait(timeout=1.0))
        release.set()
        first_thread.join(timeout=1.0)
        second_thread.join(timeout=1.0)

        self.assertGreaterEqual(max_running, 2)

    def test_queue_survives_failed_operation(self):
        queue = SessionActorQueue()
        calls = []

        def fail() -> None:
            calls.append("fail")
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            queue.run("session-1", fail)

        queue.run("session-1", lambda: calls.append("after"))
        self.assertEqual(calls, ["fail", "after"])


if __name__ == "__main__":
    unittest.main()
