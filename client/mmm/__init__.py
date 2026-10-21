"""MMM — all of Android from Python.

    from mmm import Android
    a = Android()
    a.cls("android.os.Build").MODEL
"""

from .client import Android, AndroidError, Event, JavaObject, MmmError, NotConnected
from .config import Profile

__version__ = "0.1.0"

__all__ = [
    "Android",
    "AndroidError",
    "Event",
    "JavaObject",
    "MmmError",
    "NotConnected",
    "Profile",
    "__version__",
]
