from sqlalchemy import Column, Integer, String, ForeignKey, Boolean
from sqlalchemy.orm import relationship

from inbox.models.backends.imap import ImapAccount
from inbox.models.secret import Secret

PROVIDER = 'generic'


class GenericAccount(ImapAccount):
    id = Column(Integer, ForeignKey(ImapAccount.id, ondelete='CASCADE'),
                primary_key=True)

    provider = Column(String(64))
    supports_condstore = Column(Boolean)

    # IMAP/SMTP login, if different from the email address.
    imap_username = Column(String(255), nullable=True)
    smtp_username = Column(String(255), nullable=True)

    # Secret
    password_id = Column(Integer, ForeignKey(Secret.id), nullable=False)
    secret = relationship('Secret', cascade='all', uselist=False)

    __mapper_args__ = {'polymorphic_identity': 'genericaccount'}

    @property
    def password(self):
        return self.secret.secret

    @password.setter
    def password(self, value):
        # Must be a valid UTF-8 byte sequence without NULL bytes.
        if isinstance(value, unicode):
            value = value.encode('utf-8')

        try:
            unicode(value, 'utf-8')
        except UnicodeDecodeError:
            raise ValueError('Invalid password')

        if b'\x00' in value:
            raise ValueError('Invalid password')

        if not self.secret:
            self.secret = Secret()

        self.secret.secret = value
        self.secret.type = 'password'

    @property
    def thread_cls(self):
        from inbox.models.backends.imap import ImapThread
        return ImapThread

    @property
    def actionlog_cls(self):
        from inbox.models.action_log import ActionLog
        return ActionLog

    # Override provider_info and auth_handler to make sure we always get
    # password authentication for generic accounts, even if the actual provider
    # supports other authentication mechanisms. That way, we can e.g. add
    # password-based Gmail accounts as generic accounts and simply set the
    # provider attribute to "gmail" to use the Gmail sync engine.

    @property
    def provider_info(self):
        # Make sure to copy the dict before making changes.
        provider_info = dict(super(GenericAccount, self).provider_info)
        provider_info['auth'] = 'password'
        return provider_info

    @property
    def auth_handler(self):
        from inbox.auth.base import handler_from_provider
        return handler_from_provider('custom')
