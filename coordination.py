"""One shared lock so a sync and a confirmed Slack change never run at the same time.

The sync holds it while it runs. A confirmed change holds it while it writes to Linear and saves
the matching history, so the sync that Linear's echo triggers sees nothing new to record twice.
"""
import threading

write_lock = threading.Lock()
