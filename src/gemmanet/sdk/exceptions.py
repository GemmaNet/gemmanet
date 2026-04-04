class GemmaNetError(Exception):
    pass


class AuthenticationError(GemmaNetError):
    pass


class InsufficientCreditsError(GemmaNetError):
    pass


class NoNodeAvailableError(GemmaNetError):
    pass


class ConnectionError(GemmaNetError):
    pass


class TaskTimeoutError(GemmaNetError):
    pass
