from flask import Flask, request, jsonify
from flask.ext.restful import reqparse
from werkzeug.exceptions import default_exceptions, HTTPException

from inbox.api.kellogs import APIEncoder
from inbox.log import get_logger
from inbox.models import Namespace, Account
from inbox.models.session import session_scope
from inbox.api.validation import (bounded_str, ValidatableArgument,
                                  strict_parse_args, limit)

from ns_api import app as ns_api
from ns_api import DEFAULT_LIMIT

app = Flask(__name__)
# Handle both /endpoint and /endpoint/ without redirecting.
# Note that we need to set this *before* registering the blueprint.
app.url_map.strict_slashes = False


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
        account = GmailAccount(namespace=namespace)
        account.refresh_token = data['refresh_token']
    else:
        raise ValueError('Account type not supported.')

    account.email_address = data['email_address']

    with session_scope() as db_session:
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
