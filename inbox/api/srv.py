from flask import Flask, request, jsonify
from flask.ext.restful import reqparse
from werkzeug.exceptions import default_exceptions, HTTPException

from inbox.api.kellogs import APIEncoder
from inbox.log import get_logger
from inbox.models import Namespace, Account
from inbox.models.session import session_scope
from inbox.api.validation import (bounded_str, ValidatableArgument,
                                  strict_parse_args, limit)
from inbox.api.err import APIException, InputError

from metrics_api import app as metrics_api
from ns_api import app as ns_api
from ns_api import DEFAULT_LIMIT

from inbox.webhooks.gpush_notifications import app as webhooks_api

app = Flask(__name__)
# Handle both /endpoint and /endpoint/ without redirecting.
# Note that we need to set this *before* registering the blueprint.
app.url_map.strict_slashes = False

@app.errorhandler(APIException)
def handle_input_error(error):
    response = jsonify(message=error.message, type='invalid_request_error')
    response.status_code = error.status_code
    return response


def default_json_error(ex):
    """ Exception -> flask JSON responder """
    logger = get_logger()
    logger.error('Uncaught error thrown by Flask/Werkzeug', exc_info=ex)
    response = jsonify(message=str(ex), type='api_error')
    response.status_code = (ex.code
                            if isinstance(ex, HTTPException)
                            else 500)
    return response

# Patch all error handlers in werkzeug
for code in default_exceptions.iterkeys():
    app.error_handler_spec[None][code] = default_json_error


@app.before_request
def auth():
    pass  # no auth in dev VM


@app.after_request
def finish(response):
    origin = request.headers.get('origin')
    if origin:  # means it's just a regular request
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Headers'] = 'Authorization'
        response.headers['Access-Control-Allow-Methods'] = \
            'GET,PUT,POST,DELETE,OPTIONS'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
    return response


@app.route('/n/')
def ns_all():
    """ Return all namespaces """
    # We do this outside the blueprint to support the case of an empty
    # public_id.  However, this means the before_request isn't run, so we need
    # to make our own session
    with session_scope() as db_session:
        parser = reqparse.RequestParser(argument_class=ValidatableArgument)
        parser.add_argument('limit', default=DEFAULT_LIMIT, type=limit,
                            location='args')
        parser.add_argument('offset', default=0, type=int, location='args')
        parser.add_argument('email_address', type=bounded_str, location='args')
        args = strict_parse_args(parser, request.args)

        query = db_session.query(Namespace)
        if args['email_address']:
            query = query.join(Account)
            query = query.filter_by(email_address=args['email_address'])

        query = query.limit(args['limit'])
        if args['offset']:
            query = query.offset(args['offset'])

        namespaces = query.all()
        encoder = APIEncoder()
        return encoder.jsonify(namespaces)


#
# Create a namespace
#
@app.route('/n/', methods=['POST'])
def create_namespace():
    data = request.get_json(force=True)

    namespace = Namespace()

    auth_creds = None

    if data['type'] == 'generic':
        from inbox.models.backends.generic import GenericAccount
        account = GenericAccount(namespace=namespace)
        account.imap_username = data['imap_username']
        account.imap_endpoint = data['imap_endpoint']
        account.password = data['imap_password']
        #account.smtp_username = data['smtp_username']
        #account.smtp_endpoint = data['smtp_endpoint']
        account.provider = data.get('provider', 'custom')
    elif data['type'] == 'gmail':
        from inbox.models.backends.gmail import GmailAccount
        from inbox.models.backends.gmail import GmailAuthCredentials
        from inbox.config import config

        OAUTH_CLIENT_ID = config.get_required('GOOGLE_OAUTH_CLIENT_ID')
        OAUTH_CLIENT_SECRET = config.get_required('GOOGLE_OAUTH_CLIENT_SECRET')

        account = GmailAccount(namespace=namespace)
        account.refresh_token = data['refresh_token']

        auth_creds = GmailAuthCredentials()
        auth_creds.gmailaccount = account
        auth_creds.scopes = 'https://mail.google.com/'
        auth_creds.client_id = OAUTH_CLIENT_ID
        auth_creds.client_secret = OAUTH_CLIENT_SECRET
        auth_creds.refresh_token = data['refresh_token']
        auth_creds.is_valid = True

    else:
        raise ValueError('Account type not supported.')

    account.email_address = data['email_address']

    with session_scope() as db_session:
        if auth_creds:
            db_session.add(auth_creds)
        db_session.add(account)
        db_session.commit()

        encoder = APIEncoder()
        return encoder.jsonify(namespace)

#
# Modify a namespace
#
@app.route('/n/<namespace_public_id>/', methods=['PUT'])
def modify_namespace(namespace_public_id):
    from inbox.models.backends.generic import GenericAccount
    from inbox.models.backends.gmail import GmailAccount

    with session_scope() as db_session:
        namespace = db_session.query(Namespace) \
            .filter(Namespace.public_id == namespace_public_id).one()

        account = namespace.account

        data = request.get_json(force=True)

        if isinstance(account, GenericAccount):
            if 'imap_username' in data:
                account.imap_username = data['imap_username']
            if 'imap_endpoint' in data:
                account.imap_endpoint = data['imap_endpoint']
            if 'imap_password' in data:
                account.password = data['imap_password']

            if 'refresh_token' in data:
                raise InputError('Cannot change the refresh token on a password account.')

        elif isinstance(account, GmailAccount):
            if 'refresh_token' in data:
                account.refresh_token = data['refresh_token']

            if 'imap_endpoint' in data or 'imap_username' in data or \
               'imap_password' in data:
                raise InputError('Cannot change IMAP fields on a Gmail account.')

        else:
            raise ValueError('Account type not supported.')

        db_session.add(account)
        db_session.commit()

        encoder = APIEncoder()
        return encoder.jsonify(namespace)

@app.route('/')
def home():
    return """
<html><body>
    Check out the <strong><pre style="display:inline;">docs</pre></strong>
    folder for how to use this API.
</body></html>
"""

app.register_blueprint(ns_api)  # /n/<namespace_id>/...
app.register_blueprint(metrics_api)
app.register_blueprint(webhooks_api)  # /w/...
