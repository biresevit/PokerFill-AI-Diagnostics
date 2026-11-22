"""Request-scoped timings without prompts, evidence, or provider payloads."""
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar

request_id = ContextVar("diagnosis_request_id", default=None)
logger = logging.getLogger("uvicorn.error")


@contextmanager
def stage(name):
    rid = request_id.get()
    if rid is None:
        yield
        return
    start = time.monotonic()
    logger.info("diagnosis id=%s stage=%s event=start", rid, name)
    outcome = "ok"
    try:
        yield
    except BaseException as error:
        outcome = type(error).__name__
        raise
    finally:
        logger.info("diagnosis id=%s stage=%s event=end outcome=%s seconds=%.2f",
                    rid, name, outcome, time.monotonic() - start)
