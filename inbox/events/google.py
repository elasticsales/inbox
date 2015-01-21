"""Provide Google Calendar events."""
import httplib2

from apiclient.discovery import build
from oauth2client.client import OAuth2Credentials

from inbox.basicauth import (ConnectionError, ValidationError, OAuthError)
from inbox.models.event import Event
from inbox.models.session import session_scope
from inbox.models.backends.gmail import GmailAccount
from inbox.models.backends.oauth import token_manager
from inbox.auth.gmail import (OAUTH_CLIENT_ID,
                              OAUTH_CLIENT_SECRET,
                              OAUTH_ACCESS_TOKEN_URL)
from inbox.events.util import MalformedEventError, parse_datetime, parse_date
from inbox.log import get_logger
logger = get_logger()


# Silence the stupid Google API client logger
import logging
apiclient_logger = logging.getLogger('googleapiclient.discovery')
apiclient_logger.setLevel(40)
SOURCE_APP_NAME = 'InboxApp Calendar Sync Engine'

STATUS_MAP = {'accepted': 'yes', 'needsAction': 'noreply',
              'declined': 'no', 'tentative': 'maybe'}


class GoogleEventsProvider(object):
    """
    A utility class to fetch and parse Google calendar data for the
    specified account using the Google Calendar API.

    Parameters
    ----------
    account_id: GmailAccount.id
        The user account for which to fetch event data.

    Attributes
    ----------
    google_client: gdata.calendar.client.CalendarClient
        Google API client to do the actual data fetching.
    log: logging.Logger
        Logging handler.
    """
    PROVIDER_NAME = 'google'

    def __init__(self, account_id, namespace_id):
        self.account_id = account_id
        self.namespace_id = namespace_id

        self.log = logger.new(account_id=account_id, component='event sync',
                              provider=self.PROVIDER_NAME)

    def _get_google_service(self):
        """Return the Google API client."""
        with session_scope() as db_session:
            try:
                account = db_session.query(GmailAccount).get(self.account_id)
                client_id = account.client_id or OAUTH_CLIENT_ID
                client_secret = (account.client_secret or OAUTH_CLIENT_SECRET)

                self.email = account.email_address

                access_token = token_manager.get_token(account)
                refresh_token = account.refresh_token

                credentials = OAuth2Credentials(
                    access_token=access_token,
                    client_id=client_id,
                    client_secret=client_secret,
                    refresh_token=refresh_token,
                    token_expiry=None,  # Value not actually needed by library.
                    token_uri=OAUTH_ACCESS_TOKEN_URL,
                    user_agent=SOURCE_APP_NAME)

                http = httplib2.Http()
                http = credentials.authorize(http)

                service = build(serviceName='calendar',
                                version='v3',
                                http=http)

                return service

            except OAuthError:
                self.log.error('Invalid user credentials given')
                account.mark_invalid()
                db_session.add(account)
                db_session.commit()
                raise ValidationError

            except ConnectionError:
                self.log.error('Connection error')
                account.sync_state = 'connerror'
                db_session.add(account)
                db_session.commit()
                raise ConnectionError

    def get_calendars(self, page_token=None, max_results=250):
        calendars = []

        service = self._get_google_service()

        while True:
            calendar_list = service.calendarList().list(
                showDeleted=True,
                pageToken=page_token,
                maxResults=max_results).execute()

            calendars += calendar_list['items']
            page_token = calendar_list.get('nextPageToken')

            if page_token is None:
                return [self._parse_calendar_response(c) for c in calendars]

    def _parse_calendar(_responseself, calendar):
        uid = calendar['id']
        name = calendar['summary']
        read_only = calendar['accessRole'] == 'reader'
        description = calendar.get('description', None)
        deleted = calendar.get('deleted', False)

        return dict(uid=uid, name=name, read_only=read_only,
                    description=description, deleted=deleted)

    def get_events(self, calendar_uid, sync_from_time=None):
        """
        Fetch the events for an individual calendar.

        Parameters
        ----------
            calendar_uid: the google identifier for the calendar.
                Usually username@gmail.com for the primary calendar, otherwise
                random-alphanumeric-address@google.com

        """
        events = []
        page_token = None
        service = self._get_google_service()

        while True:
            event_list = service.events().list(
                calendarId=calendar_uid,
                updatedMin=sync_from_time,
                showDeleted=True,
                pageToken=page_token,
                maxResults=2500).execute()

            events += event_list['items']
            page_token = event_list.get('nextPageToken')

            if page_token is None:
                return [self._parse_event_response(e) for e in events]

    def _parse_event_response(self, event):
        """
        Constructs an Event object from a Google calendar entry.

        Parameters
        ----------
        event: dict

        Returns
        -------
        A corresponding Event instance.

        Raises
        ------
        MalformedEventError
           If the calendar data could not be parsed correctly.

        """
        try:
            uid = str(event['id'])
            # The entirety of the raw event data in json representation.
            raw_data = str(event)
            title = event['summary']
            # Timing data
            _start = event['start']
            start = _parse_start_end(_start)

            _end = event['end']
            end = _parse_start_end(_end)

            # STOPSHIP(emfree): why is this the case?
            all_day = (_start.get('date') and _end.get('date'))

            description = event.get('description', None)
            location = event.get('location', None)

            # Ownership, read_only information
            creator = event.get('creator', None)

            owner = u'{} <{}>'.format(
                creator.get('displayName', ''), creator.get('email', '')) if \
                creator else ''

            is_owner = True if (creator and creator.get('self')) else False
            read_only = False if (is_owner or event.get('guestsCanModify')) \
                else True

            participants = []
            attendees = event.get('attendees', [])
            for attendee in attendees:
                status = STATUS_MAP.get(attendee.get['responseStatus'])
                p = dict(email_address=attendee.get('email'),
                         name=attendee.get('displayName'),
                         status=status,
                         notes=attendee.get('comment'))
                participants.append(p)

        except (KeyError, AttributeError):
            # STOPSHIP(emfree): LOL WTF?
            raise MalformedEventError()

        return Event(namespace_id=self.namespace_id,
                     uid=uid,
                     raw_data=raw_data,
                     title=title,
                     description=description,
                     location=location,
                     start=start,
                     end=end,
                     all_day=all_day,
                     owner=owner,
                     read_only=read_only,
                     participants=participants)

    def dump_event(self, event):
        """Convert an event db object to the Google API JSON format."""
        dump = {}
        dump["summary"] = event.title
        dump["description"] = event.description
        dump["location"] = event.location

        if not event.busy:
            # transparency: is the event shown in the gmail calendar as
            # as a solid or semi-transparent block.
            dump["transparency"] = "transparent"

        if event.all_day:
            dump["start"] = {"date": event.start.strftime('%Y-%m-%d')}
        else:
            dump["start"] = {"dateTime": event.start.isoformat('T'),
                             "timeZone": "UTC"}
            dump["end"] = {"dateTime": event.end.isoformat('T'),
                           "timeZone": "UTC"}

        if event.participants:
            attendees = [self._create_attendee(participant) for participant
                         in event.participants]
            dump["attendees"] = [attendee for attendee in attendees
                                 if attendee]

        return dump

    def _create_attendee(self, participant):
        inv_status_map = {value: key for key, value in
                          STATUS_MAP.iteritems()}

        att = {}
        if 'name' in participant:
            att["displayName"] = participant['name']

            if 'status' in participant:
                att["responseStatus"] = inv_status_map[participant['status']]

            if 'email' in participant:
                att["email"] = participant['email']

            if 'guests' in participant:
                att["additionalGuests"] = participant['guests']

        return att


def _parse_start_end(field):
    dt = field.get('dateTime')
    # STOPSHIP(emfree) do we need to parse the timezone?
    #tz = field.get('timeZone')
    date = field.get('date')

    return parse_datetime(dt) if dt else parse_date(date)
