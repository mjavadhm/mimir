"""Linux guardian kills the entire agent process group if its parent disappears."""


def main() -> None:
    import ctypes
    import os
    import signal
    import sys

    parent = int(sys.argv[1])
    child = 0

    def stop(signum: int, frame: object) -> None:
        if child:
            try:
                os.killpg(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, stop)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Unable to set parent-death signal")
    if os.getppid() != parent:
        raise SystemExit(1)
    # Block death handling across fork to prevent an orphan in the fork/assignment window.
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
    child = os.fork()
    if child == 0:
        os.setsid()
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM})
        if os.getppid() == 1:
            raise SystemExit(1)
        os.execvpe(sys.argv[2], sys.argv[2:], os.environ)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM})
    try:
        _, status = os.waitpid(child, 0)
        raise SystemExit(os.waitstatus_to_exitcode(status))
    finally:
        try:
            os.killpg(child, signal.SIGKILL)
        except ProcessLookupError:
            pass


if __name__ == "__main__":
    main()
