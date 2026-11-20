"""Classified API failures, without credential-bearing HTTP payloads."""


class GithubPermanent(Exception):
    pass


class GithubTemporary(Exception):
    def __init__(self, retry_after: float = 0) -> None:
        self.retry_after = retry_after
        super().__init__("GitHub temporarily unavailable")


class GithubUnknown(Exception):
    pass
