import calendar
from inbox.heartbeat.config import get_redis_client
import dateutil.parser
from datetime import datetime, timedelta
from sqlalchemy.orm import subqueryload, load_only, joinedload
from sqlalchemy.exc import IntegrityError, OperationalError, InvalidRequestError
from sqlalchemy.orm.exc import ObjectDeletedError, StaleDataError
from sqlalchemy import asc, func
from inbox.config import config
from inbox.heartbeat.config import get_redis_client
from inbox.models import Account, Message, Folder, Category, Label, Namespace
from inbox.models.backends.imap import ImapUid
from inbox.models.session import session_scope
from nylas.logging import configure_logging, get_logger
from redis import Redis
import tasktiger
from tasktiger import retry
from tasktiger.exceptions import JobTimeoutException
import time

configure_logging()
log = get_logger()

TASKTIGER_DATABASE = 10
MIGRATION_DATABASE = 11

REQUEUE_TIME = 60

tiger = tasktiger.TaskTiger(connection=get_redis_client(db=TASKTIGER_DATABASE,
                                                        strict=False))

def select_category(categories):
    # TODO[k]: Implement proper ranking function
    return list(categories)[0]

def update_metadata(message):
    account = message.namespace.account
    if account.discriminator == 'easaccount':
        uids = message.easuids
    else:
        uids = message.imapuids

    message.is_read = any(i.is_seen for i in uids)
    message.is_starred = any(i.is_flagged for i in uids)

    categories = set()
    for i in uids:
        categories.update(i.categories)

    if account.category_type == 'folder':
        categories = [select_category(categories)] if categories else []

    message.categories = categories



def populate_labels(uid, account, db_session):
    if uid.g_labels is None:
        return
    existing_labels = {
        (l.name, l.canonical_name): l for l in account.labels
    }
    uid.is_draft = '\\Draft' in uid.g_labels
    uid.is_starred = '\\Starred' in uid.g_labels

    category_map = {
        '\\Inbox': 'inbox',
        '\\Important': 'important',
        '\\Sent': 'sent'
    }

    remote_labels = set()
    for label_string in uid.g_labels:
        if label_string in ('\\Draft', '\\Starred'):
            continue
        elif label_string in category_map:
            remote_labels.add((category_map[label_string],
                               category_map[label_string]))
        else:
            remote_labels.add((label_string, None))

    uid.labels = set()
    for key in remote_labels:
        if key not in existing_labels:
            label = Label.find_or_create(db_session, account, key[0], key[1])
            uid.labels.add(label)
            account.labels.append(label)
        else:
            uid.labels.add(existing_labels[key])


def set_labels_for_imapuids(account, db_session):
    timer = time.time()
    redis = get_redis_client(db=MIGRATION_DATABASE)
    account_id = account.id

    updated_since_ts = redis.get('l:%s' % account_id)
    if updated_since_ts:
        updated_since = datetime.utcfromtimestamp(int(updated_since_ts))
    else:
        updated_since = None

    uids = db_session.query(ImapUid).filter(
        ImapUid.account_id == account.id).options(
            subqueryload(ImapUid.labelitems).joinedload('label')). \
            order_by(asc(ImapUid.updated_at))
    if updated_since is not None:
        uids = uids.filter(ImapUid.updated_at >= updated_since)
    count = 0
    uid = None
    for uid in uids:
        count += 1
        try:
            uid_id = uid.id
            if updated_since is not None and uid.updated_at < updated_since:
                continue
            populate_labels(uid, account, db_session)
            if updated_since is not None and uid.message is not None:
                # Make sure we subsequently also recompute the message's
                # categories.
                uid.message.updated_at = datetime.utcnow()
            log.info('Updated UID labels', account_id=account.id, uid=uid.id)
            if not count % 200:
                db_session.commit()

                new_updated_since = uid.updated_at
                if new_updated_since == updated_since:
                    log.info('imapuids: updated date did not increase at %s' % \
                             updated_since, account_id=account_id)

                can_requeue = new_updated_since != updated_since

                updated_since = new_updated_since
                updated_since_ts = calendar.timegm(updated_since.timetuple())
                redis.set('l:%s' % account_id, updated_since_ts)

                if can_requeue and time.time() - timer > REQUEUE_TIME:
                    log.info('Soft time limit exceeded in migrate_account_metadata, requeueing', account_id=account_id)
                    migrate_account.delay(account_id)
                    return False

        except (IntegrityError, ObjectDeletedError) as exc:
            log.error('Error updating uid', uid=uid_id, account_id=account_id,
                      exc=exc)
            raise

    if uid:
        db_session.commit()
        updated_since = uid.updated_at
        updated_since_ts = calendar.timegm(updated_since.timetuple())
        redis.set('l:%s' % account_id, updated_since_ts)

    return True


def create_categories_for_imap_folders(account, db_session):
    for folder in db_session.query(Folder).filter(
            Folder.account_id == account.id):
        cat = Category.find_or_create(
            db_session, namespace_id=account.namespace.id,
            name=folder.canonical_name, display_name=folder.name,
            type_='folder')
        folder.category = cat
    db_session.commit()


def create_categories_for_gmail_folders(account, db_session):
    default_folder_name = {
        'all': '[Gmail]/All Mail',
        'spam': '[Gmail]/Spam',
        'trash': '[Gmail]/Trash',
    }
    for folder in db_session.query(Folder).filter(
            Folder.account_id == account.id):
        if folder.canonical_name in ('all', 'spam', 'trash'):
            cat = Category.find_or_create(
                db_session, namespace_id=account.namespace.id,
                name=folder.canonical_name,
                display_name=folder.name or default_folder_name[folder.canonical_name],
                type_='folder')
            folder.category = cat
        if folder.name is not None:
            Label.find_or_create(db_session, account, folder.name,
                                 folder.canonical_name)
    db_session.commit()


def create_categories_for_easfoldersyncstatuses(account, db_session):
    from inbox.mailsync.backends.eas.base.foldersync import save_categories
    if not account.foldersyncstatuses:
        return

    save_categories(db_session, account, account.primary_device_id)
    db_session.commit()
    save_categories(db_session, account, account.secondary_device_id)


def migrate_account_metadata(account_id):
    with session_scope(versioned=False) as db_session:
        account = db_session.query(Account).get(account_id)
        try:
            provider = account.provider
        except ObjectDeletedError:
            log.error('Account has no namespace', account_id=account_id)
            return False

        if provider == 'gmail':
            create_categories_for_gmail_folders(account, db_session)
            if not set_labels_for_imapuids(account, db_session):
                return False # requeueing
        else:
            create_categories_for_imap_folders(account, db_session)
        db_session.commit()

    return True


@tiger.task(retry_on=[OperationalError, InvalidRequestError],
            retry_method=retry.fixed(60, 100),
            hard_timeout=1200)
def migrate_messages(account_id, message_ids):
    with session_scope(versioned=False) as db_session:
        namespace = db_session.query(Namespace).filter_by(
            account_id=account_id).one()
        messages = db_session.query(Message). \
                    filter(Message.id.in_(message_ids)). \
                    filter(Message.namespace_id == namespace.id,
                           Message.deleted_at == None)
        messages = messages.options(
            load_only(Message.id, Message.is_read, Message.is_starred,
                      Message.is_draft, Message.updated_at),
            joinedload(Message.namespace).load_only('id'),
            subqueryload(Message.imapuids),
            subqueryload(Message.messagecategories)). \
            all()

        message_id = None

        try:
            if not messages:
                return
            for message in messages:
                message_id = message.id
                try:
                    update_metadata(message)
                except IndexError:
                    # Can happen for messages without a folder.
                    # Raise for now so we know.
                    raise
                log.info('Updated message', namespace_id=namespace.id,
                         message_id=message.id)
            db_session.commit()
        except (IntegrityError, ObjectDeletedError, StaleDataError) as exc:
            log.error('Error migrating messages', account_id=account_id,
                      message_id=message_id, exc=exc)
            raise


def migrate_account_messages(account_id):
    timer = time.time()

    redis = get_redis_client(db=MIGRATION_DATABASE)

    INITIAL_LIMIT = 500

    limit = INITIAL_LIMIT

    updated_since_ts = redis.get('u:%s' % account_id)
    if updated_since_ts:
        updated_since = datetime.utcfromtimestamp(int(updated_since_ts))
    else:
        updated_since = None

    with session_scope(versioned=False) as db_session:
        namespace = db_session.query(Namespace).filter_by(
            account_id=account_id).one()
        while True:
            try:
                messages = db_session.query(Message). \
                    filter(Message.namespace_id == namespace.id,
                           Message.deleted_at == None)
                if updated_since:
                    messages = messages.filter(Message.updated_at >= updated_since)
                messages = messages.options(
                    load_only(Message.id, Message.updated_at)). \
                    with_hint(Message,
                              'USE INDEX (ix_message_namespace_id_deleted_at)'). \
                    order_by(asc(Message.updated_at)).limit(limit).all()

                log.info('Queueing messages', account_id=account_id,
                                              updated_since=updated_since_ts)

                if not messages:
                    break

                tiger.delay(migrate_messages,
                            args=(account_id, [message.id for message in messages]),
                            queue='migrate_messages.%s' % account_id)

                new_updated_since = max(message.updated_at for message in messages)

                if new_updated_since == updated_since and len(messages) >= limit:
                    log.info('updated date did not increase at %s' % \
                             updated_since, account_id=account_id)
                    limit *= 2
                    if limit > INITIAL_LIMIT * 8:
                        raise RuntimeError('updated date did not increase for account %s: %s' %
                                           (account_id, updated_since))
                    continue
                else:
                    if limit > INITIAL_LIMIT:
                        limit = INITIAL_LIMIT

                updated_since = new_updated_since
                updated_since_ts = calendar.timegm(updated_since.timetuple())
                redis.set('u:%s' % account_id, updated_since_ts)

                if len(messages) < limit:
                    break

                if time.time() - timer > REQUEUE_TIME:
                    log.info('Soft time limit exceeded in migrate_account_messages, requeueing', account_id=account_id)
                    migrate_account.delay(account_id)
                    return

            except (IntegrityError, ObjectDeletedError, StaleDataError) as exc:
                log.error('Error fetching messages', account_id=account_id,
                          exc=exc)
                raise


@tiger.task(unique=True,
            lock=True,
            retry_on=[InvalidRequestError, JobTimeoutException,
                      OperationalError],
            retry_method=retry.fixed(60, 100),
            hard_timeout=1200)
def migrate_account(account_id):
    log.info('Migrating account', account_id=account_id)
    if migrate_account_metadata(account_id):
        migrate_account_messages(account_id)
        log.info('Migrated account', account_id=account_id)

def migrate_accounts():
    with session_scope() as db_session:
        accounts = db_session.query(Account)
        for account in accounts:
            if not account.is_deleted:
                migrate_account.delay(account.id)
