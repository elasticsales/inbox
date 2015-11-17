from inbox.heartbeat.config import get_redis_client
from inbox.models.session import session_scope
from inbox.models import Account
from inbox.models.util import delete_namespace
from inbox.heartbeat.status import clear_heartbeat_status
import tasktiger
import time

TASKTIGER_DATABASE = 10

tiger = tasktiger.TaskTiger(connection=get_redis_client(db=TASKTIGER_DATABASE,
                                                        strict=False))

@tiger.task()
def delete_account_data(account_id):
    with session_scope() as db_session:
        account = db_session.query(Account).get(account_id)

        if not account:
            print 'Account with id {} does NOT exist.'.format(account_id)
            return

        account_id = account.id
        namespace_id = account.namespace.id

        if account.sync_should_run:
            print 'Account with id {} should be running.\n'\
                  'Will NOT delete.'.format(account_id)
            return

        print 'Deleting account with id: {}...'.format(account_id)

        start = time.time()

        # Delete data in database
        print 'Deleting database data'
        delete_namespace(account_id, namespace_id)

        database_end = time.time()
        print 'Database data deleted. Time taken: {}'.\
            format(database_end - start)

        # Delete liveness data
        print 'Deleting liveness data'
        clear_heartbeat_status(account_id)

        end = time.time()
        print 'All data deleted successfully! TOTAL time taken: {}'.\
            format(end - start)
