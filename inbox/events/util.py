import dateutil.parser as date_parser
from dateutil import tz


class MalformedEventError(Exception):
    pass


def parse_datetime(datetime):
    if not datetime:
        raise MalformedEventError()

    try:
        dt = date_parser.parse(datetime)
        return dt.astimezone(tz.gettz('UTC')).replace(tzinfo=None)
    except ValueError:
        raise MalformedEventError()


def parse_date(date):
    if not date:
        raise MalformedEventError

    try:
        date_parser.parse(date)
    except ValueError:
        raise MalformedEventError()
