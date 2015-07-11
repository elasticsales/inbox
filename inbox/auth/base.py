from abc import ABCMeta, abstractmethod
from imapclient import IMAPClient
from inbox.providers import providers
from inbox.basicauth import NotSupportedError
from inbox.log import get_logger
import socket
from socket import gaierror, error as socket_error
log = get_logger()


def handler_from_provider(provider_name):
    """Return an authentication handler for the given provider.

    Parameters
    ----------
    provider_name : str
        Name of the email provider (e.g. inbox/providers.py) OR of the provider
        module's PROVIDER constant

        (XXX: interface terribleness!)

    Returns
    -------
    An object that implements the AuthHandler interface.
    """
    from inbox.auth import module_registry
    auth_mod = module_registry.get(provider_name)

    if auth_mod is None:
        # Try to get a generic provider
        info = providers.get(provider_name, None)
        if info:
            provider_type = info.get('type', None)
            if provider_type:
                auth_mod = module_registry.get('generic')

    if auth_mod is None:
        raise NotSupportedError('Inbox does not support the email provider.')

    auth_handler_class = getattr(auth_mod, auth_mod.AUTH_HANDLER_CLS)
    auth_handler = auth_handler_class(provider_name=provider_name)
    return auth_handler


class AuthHandler(object):
    __metaclass__ = ABCMeta

    def __init__(self, provider_name):
        self.provider_name = provider_name

    def connect_to_imap(self, account, read_timeout=300):
        host, port, is_secure = account.imap_endpoint
        try:
            conn = IMAPClient(host, port=port, use_uid=True, ssl=(port == 993),
                              read_timeout=read_timeout)
            if is_secure and port != 993:
                # Raises an exception if TLS can't be established
                conn._imap.starttls()
        except (IMAPClient.Error, socket.error) as exc:
            log.error('Error instantiating IMAP connection',
                      account_id=account.id,
                      email=account.email_address,
                      host=host,
                      port=port,
                      error=exc)
            raise
        return conn

    # optional
    def connect_account(self, account):
        """Return an authenticated IMAPClient instance for the given account.

        This is an optional interface, which is only needed for accounts that
        are synced using IMAP.
        """
        raise NotImplementedError

    @abstractmethod
    def create_account(self, db_session, email_address, response):
        raise NotImplementedError

    @abstractmethod
    def verify_account(self, account):
        raise NotImplementedError

    @abstractmethod
    def interactive_auth(self, email_address=None):
        raise NotImplementedError
