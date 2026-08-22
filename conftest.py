import gc
import os

# See virtaal.main.Virtaal.__init__ for the full explanation: PyGObject-
# wrapped GTK widgets end up in Python reference cycles, and GTK's C-level
# widget dispose/unparent chain is not reentrant, so letting the cyclic GC
# free one at an arbitrary point segfaults deep inside GTK. The test suite
# builds and tears down a lot of widgets, so it hits this reliably -
# reproduced directly: about half of repeated `pytest virtaal/` runs
# crashed (SIGSEGV) with the collector left on.
gc.disable()


def pytest_sessionfinish(session, exitstatus):
    # CPython's normal interpreter finalization forces one last
    # gc.collect() regardless of gc.disable() above, which reproduces the
    # same crash at process exit instead of during the run (every test
    # already passed by this point - dots print, then SIGSEGV). Skip
    # normal finalization; there's nothing left to flush or clean up.
    os._exit(exitstatus)
