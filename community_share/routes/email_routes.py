import re
import logging
import hashlib, hmac

from flask import request

from community_share.models.conversation import Message
from community_share.mail import Email, get_mailer
from community_share.mail_actions import append_conversation_link
from community_share import config, store
from community_share.routes import base_routes

logger = logging.getLogger(__name__)


def verify(api_key, token, timestamp, signature):
    return signature == hmac.new(
        key=bytearray(api_key, 'utf8'),
        msg=bytearray('{}{}'.format(timestamp, token), 'utf8'),
        digestmod=hashlib.sha256).hexdigest()

def register_email_routes(app):

    @app.route('/api/email', methods=['POST'])
    def receive_email():

        if config.MAILER_TYPE == 'MAILGUN':
            verify = True
        else:
            verify = False
        email = Email.from_mailgun_data(request.values, verify=verify)

        address_pattern = '^replytomessage([0-9]+)@{mailgun_domain}$'.format(
            mailgun_domain=config.MAILGUN_DOMAIN)
        match = re.search(address_pattern, email.to_address)
        message = None
        if match:
            message_id = int(match.groups()[0])
            message = store.session.query(Message).filter_by(id=message_id).first()
        if message is None:
            logger.warning('Received an email but did not find corresponding message.')
        else:
            # Create a new message
            new_message = Message(
                conversation_id=message.conversation_id,
                sender_user_id=message.receiver_user().id,
                content=email.new_content)
            store.session.add(new_message)
            store.session.commit()
            # Create an email to send to the recipient
            forward_to_address = message.sender_user.email
            forward_from_address = 'replytomessage{0}@{mailgun_domain}'.format(
                new_message.id, mailgun_domain=config.MAILGUN_DOMAIN)
            forward_content = append_conversation_link(
                email.content, message.conversation)
            forward_new_content = append_conversation_link(
                email.new_content, message.conversation)
            forward_email = Email(from_address=forward_from_address,
                                  to_address=forward_to_address,
                                  subject=email.subject,
                                  content=forward_content,
                                  new_content=forward_new_content,
            )
            error_message = get_mailer().send(forward_email)
        response = base_routes.make_OK_response()
        return response
