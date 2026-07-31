"""Typed content-free ACP gateway failures."""


class AcpError(RuntimeError):
    """Base failure for the generic ACP gateway."""


class AcpProcessError(AcpError):
    """The admitted local process could not be safely started or retained."""


class AcpProtocolError(AcpError):
    """The peer violated the reviewed ACP wire contract."""


class AcpProtocolVersionError(AcpProtocolError):
    """The peer selected an unsupported ACP wire version."""


class AcpCapabilityError(AcpProtocolError):
    """A requested optional method was not negotiated."""


class AcpLifecycleError(AcpError):
    """An operation was attempted in the wrong connection lifecycle state."""


class AcpPermissionError(AcpError):
    """An ACP permission request or response failed authority binding."""


class AcpRequestCancelled(AcpError):
    """A caller cancelled a local waiter before the agent completed."""


class AcpRequestTimeout(AcpError):
    """An ACP waiter exceeded its explicit caller deadline."""
