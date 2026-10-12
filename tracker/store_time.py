from datetime import datetime


def timestamp():
    return datetime.now().astimezone().isoformat(timespec='seconds')
