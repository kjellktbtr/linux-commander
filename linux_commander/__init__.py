"""linux-commander: a dual-pane orthodox file manager built with plain tkinter."""

from linux_commander.dialogs import (
    CompressionDialog,
    ProgressDialog,
    choose_from_list,
    confirm,
    date_time_picker,
    error,
    pattern_dialog,
    prompt,
    run_with_progress,
    show_text,
)
from linux_commander.search_dialog import SearchCriteria as SearchDialogCriteria
from linux_commander.search_dialog import SearchDialog
from linux_commander.search_engine import SearchCriteria, SearchResult, search_files
from linux_commander.settings import FtpSession, Settings, load_settings, save_settings

__version__ = "0.1.0"

__all__ = [
    "Settings",
    "FtpSession",
    "load_settings",
    "save_settings",
    "SearchDialog",
    "SearchDialogCriteria",
    "SearchCriteria",
    "SearchResult",
    "search_files",
    "CompressionDialog",
    "ProgressDialog",
    "choose_from_list",
    "confirm",
    "date_time_picker",
    "error",
    "pattern_dialog",
    "prompt",
    "run_with_progress",
    "show_text",
]
