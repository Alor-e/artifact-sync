class RetryableError(Exception):
    """Base class for errors that should trigger retries"""
    pass

class NonRetryableError(Exception):
    """Base class for errors that should not trigger retries"""
    pass