from collections import defaultdict
from flask import Blueprint, jsonify, request
from operator import itemgetter

from inbox.api.kellogs import APIEncoder
from inbox.heartbeat.status import get_heartbeat_status
from inbox.models import Folder, Account, Namespace
from inbox.models.backends.imap import ImapFolderSyncStatus
from inbox.models.session import session_scope

app = Blueprint(
    'metrics_api',
    __name__,
    url_prefix='/metrics')

@app.route('/')
def index():
    with session_scope() as db_session:
        if 'namespace_id' in request.args:
            namespace = db_session.query(Namespace).filter(
                    Namespace.public_id == request.args['namespace_id']).one()
        else:
            namespace = None

        accounts = db_session.query(Account)
        if namespace:
            accounts = accounts.filter(Account.namespace == namespace)

        accounts = list(accounts)

        if len(accounts) == 1:
            heartbeat = get_heartbeat_status(account_id=accounts[0].id)
            folder_sync_statuses = db_session.query(ImapFolderSyncStatus). \
                    filter(ImapFolderSyncStatus.account_id==accounts[0].id). \
                    join(Folder)
        else:
            heartbeat = get_heartbeat_status()
            folder_sync_statuses = db_session.query(ImapFolderSyncStatus). \
                    join(Folder)

        data = []

        folder_data = defaultdict(dict)

        for folder_sync_status in folder_sync_statuses:
            folder_data[folder_sync_status.account_id][folder_sync_status.folder_id] = {
                'remote_uid_count': folder_sync_status.metrics.get('remote_uid_count'),
                'download_uid_count': folder_sync_status.metrics.get('download_uid_count'),
                'state': folder_sync_status.state,
                'name': folder_sync_status.folder.name,
                'alive': False,
                'heartbeat_at': None
            }

        for account in accounts:
            if account.id in heartbeat:
                account_heartbeat = heartbeat[account.id]
                account_folder_data = folder_data[account.id]
                alive = account_heartbeat.alive
                for folder_status in account_heartbeat.folders:
                    if folder_status.id in account_folder_data:
                        device = folder_status.devices[0]
                        account_folder_data[folder_status.id].update({
                            'alive': folder_status.alive,
                            'heartbeat_at': device.heartbeat_at,
                        })

                initial_sync = account_heartbeat.initial_sync or \
                        any(f['state'] == 'initial' for f in account_folder_data.values())

                total_uids = sum(f['remote_uid_count'] or 0 for f in account_folder_data.values())
                remaining_uids = sum(f['download_uid_count'] or 0 for f in account_folder_data.values())
                if total_uids:
                    progress = 100. / total_uids * (total_uids - remaining_uids)
                else:
                    progress = None
            else:
                alive = False
                initial_sync = None
                progress = None

            sync_status = account.sync_status
            if not 'original_start_time' in sync_status or not account.id in heartbeat:
                sync_status = 'starting'
            elif alive:
                if initial_sync:
                    sync_status = 'initial'
                else:
                    sync_status = 'running'
            elif sync_status['state'] == 'running':
                if initial_sync:
                    sync_status = 'initial delayed'
                else:
                    sync_status = 'delayed'
            else:
                sync_status = 'dead'

            data.append({
                'account_id': account.public_id,
                'namespace_id': account.namespace.public_id,
                'alive': alive,
                'initial_sync': initial_sync,
                'provider_name': account.provider,
                'email_address': account.email_address,
                'folders': sorted(folder_data[account.id].values(), key=itemgetter('name')),
                'sync_status': sync_status,
                'progress': progress,
            })

        return APIEncoder().jsonify(data)
