"""Coverage subprocess hook (see tests/.coveragerc).

tests/run_all.py --coverage prepends this directory to PYTHONPATH so that
every Python process started while the suite runs — the per-suite processes,
the offscreen UI phases and the scratch app copy they launch — calls
coverage.process_startup() at interpreter startup and measures itself.

process_startup() is a no-op unless COVERAGE_PROCESS_START is set in the
environment, so importing this module outside a coverage run does nothing.
"""
try:
    import coverage
except ImportError:      # coverage not installed: plain test run, no-op
    pass
else:
    coverage.process_startup()
