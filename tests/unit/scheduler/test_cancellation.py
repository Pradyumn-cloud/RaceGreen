
from racegreen.scheduler.cancellation import CancellationCoordinator, ProcessRecord


def test_idempotent_cancel_and_registry():
    c = CancellationCoordinator()
    c.request_cancel()
    c.request_cancel()  # idempotent
    assert c.is_cancelled
    rec = ProcessRecord(pid=12345, pgid=12345, run_id="r1", agent_id="a1", kind="test")
    c.register_process(rec)
    procs = c.get_processes()
    assert len(procs) == 1
    c.unregister_process(12345)
    assert len(c.get_processes()) == 0


def test_late_rejection_recorded():
    c = CancellationCoordinator()
    c.record_late_rejection()
    c.record_late_rejection()
    assert c._late_rejections == 2
