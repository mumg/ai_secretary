from enum import StrEnum


class TaskStatus(StrEnum):
    NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    POSSIBLY_COMPLETED = "POSSIBLY_COMPLETED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class TaskPriority(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class PrioritySource(StrEnum):
    MANUAL = "MANUAL"
    LLM = "LLM"
    SOURCE = "SOURCE"


class AnalysisState(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    IGNORED = "IGNORED"
    FAILED = "FAILED"


class ChatRequestStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Direction(StrEnum):
    INCOMING = "INCOMING"
    OUTGOING = "OUTGOING"
    INTERNAL = "INTERNAL"


class AttachmentState(StrEnum):
    PENDING = "PENDING"
    EXTRACTED = "EXTRACTED"
    UNSUPPORTED = "UNSUPPORTED"
    FAILED = "FAILED"


class ComponentHealthStatus(StrEnum):
    OK = "OK"
    BUSY = "BUSY"
    DEGRADED = "DEGRADED"
    ERROR = "ERROR"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"
    DISABLED = "DISABLED"
