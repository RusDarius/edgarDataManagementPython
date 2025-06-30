import sys
import io


def accept_utf8_encoding():
    if hasattr(sys.stdout, "detach"):
        # Tell the type checker that we're narrowing the type
        stdout_detached = sys.stdout.detach()  # type: ignore[attr-defined]
        sys.stdout = io.TextIOWrapper(stdout_detached, encoding="utf-8")
