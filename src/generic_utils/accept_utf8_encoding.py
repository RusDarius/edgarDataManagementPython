import sys
import io


def accept_utf8_encoding():
    """Make stdout UTF-8.

    Reconfigures the existing stream in place when it can (no detach), so a
    stream owned by someone else (pytest capture, an IDE console) keeps working.
    Falls back to the old detach-and-rewrap only for the real process stdout.
    """
    stream = sys.stdout
    encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
    if encoding == "utf8":
        return
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8")
            return
        except (ValueError, io.UnsupportedOperation):
            pass
    if stream is sys.__stdout__ and hasattr(stream, "detach"):
        # Tell the type checker that we're narrowing the type
        stdout_detached = stream.detach()  # type: ignore[attr-defined]
        sys.stdout = io.TextIOWrapper(stdout_detached, encoding="utf-8")
