"""ACP JSON-lines framing with the release message bound."""

from gigaloom.harnesses.acp.contracts import AcpLimits
from gigaloom.structured_processes import JsonLineFrameDecoder


class AcpFrameDecoder(JsonLineFrameDecoder):
    """Decode ACP frames with the explicit 0.7 default instead of an SDK default."""

    def __init__(self, *, max_message_bytes: int | None = None) -> None:
        limit = (
            AcpLimits().max_message_bytes
            if max_message_bytes is None
            else max_message_bytes
        )
        super().__init__(max_frame_bytes=limit)
