from datetime import datetime
from collections import Counter

from inbox.log import get_logger
logger = get_logger()

from inbox.sync.base_sync import BaseSyncMonitor
from inbox.models import Event, Account
from inbox.util.debug import bind_context
from inbox.models.session import session_scope
from inbox.basicauth import ValidationError
from inbox.util.misc import MergeError

from inbox.events.google import GoogleEventsProvider


EVENT_SYNC_FOLDER_ID = -2
EVENT_SYNC_FOLDER_NAME = 'Events'


class EventSync(BaseSyncMonitor):
    """Per-account event sync engine.

    Parameters
    ----------
    account_id: int
        The ID for the user account for which to fetch event data.

    poll_frequency: int
        In seconds, the polling frequency for querying the events provider
        for updates.

    Attributes
    ---------
    log: logging.Logger
        Logging handler.

    """
    def __init__(self, email_address, provider_name, account_id, namespace_id,
                 poll_frequency=300):
        bind_context(self, 'eventsync', account_id)
        self.log = logger.new(account_id=account_id, component='event sync')
        self.log.info('Begin syncing Events...')
        self.provider_name = provider_name
        self.folder_id = EVENT_SYNC_FOLDER_ID
        self.folder_name = EVENT_SYNC_FOLDER_NAME
        self.email_address = email_address

        self.provider = GoogleEventsProvider(account_id, namespace_id)

        BaseSyncMonitor.__init__(self,
                                 account_id,
                                 namespace_id,
                                 EVENT_SYNC_FOLDER_ID,
                                 poll_frequency=poll_frequency,
                                 retry_fail_classes=[ValidationError])

    def sync(self):
        """Query a remote provider for updates and persist them to the
        database. This function runs every `self.poll_frequency`.
        """
        # Get a timestamp before polling, so that we don't subsequently miss remote
        # updates that happen while the poll loop is executing.
        sync_timestamp = datetime.utcnow()
        provider_name = self.provider

        with session_scope() as db_session:
            account = db_session.query(Account).get(account_id)
            last_sync = None
            if account.last_synced_events is not None:
                # Note explicit offset is required by e.g. Google calendar API.
                last_sync = datetime.isoformat(account.last_synced_events) + 'Z'

        calendars = provider_instance.get_calendars(last_sync)
        calendar_ids = _sync_calendars(account_id, calendars, log, provider_name)

        for (uid, id_) in calendar_ids:
            events = provider_instance.get_events(uid, sync_from_time=last_sync)
            _sync_events(account_id, id_, events, log, provider_name)

        with session_scope() as db_session:
            account.last_synced_events = sync_timestamp
            db_session.commit()


def _sync_calendars(account_id, calendars, log, provider_name):
    ids_ = []

    with session_scope() as db_session:
        account = db_session.query(Account).get(account_id)
        namespace_id = account.namespace.id

        change_counter = Counter()
        for c in calendars:
            uid = c['uid']
            assert uid is not None, 'Got remote item with null uid'

            local = db_session.query(Calendar).filter(
                Calendar.namespace == account.namespace,
                Calendar.uid == uid).first()

            if local is not None:
                if c['deleted']:
                    db_session.delete(local)
                    change_counter['deleted'] += 1
                else:
                    local.update(c)
                    change_counter['updated'] += 1
            else:
                local = Calendar(namespace_id=namespace_id,
                                 uid=uid,
                                 provider_name=provider_name)
                local.update(c)
                db_session.add(local)
                db_session.flush()
                change_counter['added'] += 1

            ids_.append((uid, local.id))

        log.info('calendar sync',
                 added=change_counter['added'],
                 updated=change_counter['updated'],
                 deleted=change_counter['deleted'])

        db_session.commit()

    return ids_


def _sync_events(account_id, calendar_id, events, log, provider_name):
    with session_scope() as db_session:
        account = db_session.query(Account).get(account_id)
        namespace_id = account.namespace.id

        change_counter = Counter()
        for e in events:
            uid = e['uid']
            assert uid is not None, 'Got remote item with null uid'

            local = db_session.query(Event).filter(
                Event.namespace == account.namespace,
                Event.calendar_id == calendar_id,
                Event.uid == uid).first()

            if local is not None:
                if e['deleted']:
                    db_session.delete(local)
                    change_counter['deleted'] += 1
                else:
                    local.update(db_session, e)
                    change_counter['updated'] += 1
            else:
                local = Event(namespace_id=namespace_id,
                              calendar_id=calendar_id,
                              uid=uid,
                              provider_name=provider_name)
                local.update(db_session, e)
                db_session.add(local)
                db_session.flush()
                change_counter['added'] += 1

            log.info('event sync',
                     calendar_id=calendar_id,
                     added=change_counter['added'],
                     updated=change_counter['updated'],
                     deleted=change_counter['deleted'])

        db_session.commit()
