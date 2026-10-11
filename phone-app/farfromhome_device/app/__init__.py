"""Process entry points.

Each function is a long-running process from the brief's process model:
    agent   -> run_agent()
    engine  -> run_engine()
    ui      -> run_ui()
    syncd   -> run_syncd()

They are intentionally importable so `run.py --demo` can start all four
in one process for laptop testing (see docs/DECISIONS.md D-03).
"""
