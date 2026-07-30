"""Public scheduled automation facade."""

from .constants import ACTIVE_OCCURRENCE_STATUSES as ACTIVE_OCCURRENCE_STATUSES
from .constants import DEFAULT_PREVIEW_COUNT as DEFAULT_PREVIEW_COUNT
from .constants import SCHEDULE_DIRECTORY as SCHEDULE_DIRECTORY
from .models import ScheduleConflictError as ScheduleConflictError
from .models import ScheduleDefinition as ScheduleDefinition
from .models import ScheduleError as ScheduleError
from .models import ScheduleOccurrence as ScheduleOccurrence
from .definitions import build_schedule_definition as build_schedule_definition
from .definitions import discover_schedules as discover_schedules
from .definitions import load_schedule as load_schedule
from .definitions import next_occurrences as next_occurrences
from .definitions import occurrence_to_dict as occurrence_to_dict
from .definitions import save_schedule as save_schedule
from .definitions import schedule_definition_to_dict as schedule_definition_to_dict
from .service import ScheduleService as ScheduleService

__all__ = [
    "ACTIVE_OCCURRENCE_STATUSES",
    "DEFAULT_PREVIEW_COUNT",
    "SCHEDULE_DIRECTORY",
    "ScheduleConflictError",
    "ScheduleDefinition",
    "ScheduleError",
    "ScheduleOccurrence",
    "ScheduleService",
    "build_schedule_definition",
    "discover_schedules",
    "load_schedule",
    "next_occurrences",
    "occurrence_to_dict",
    "save_schedule",
    "schedule_definition_to_dict",
]
