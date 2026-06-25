"""Processing layer: Celery app, the heavy ``ml`` worker, and the reminder scheduler.

The heavy worker imports and runs the existing ``clauseops`` package without
rewriting the pipeline. Populated by later tasks.
"""
