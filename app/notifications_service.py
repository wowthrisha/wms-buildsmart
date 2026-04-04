"""
notifications_service.py — Phase 6: Twilio WhatsApp + Resend email helpers
Each function is fire-and-forget with graceful failure.
"""
import os
import logging
from app.models import Notification, db

logger = logging.getLogger(__name__)

def notify_user(user_id: int, title: str, body: str):
    """Create in-app DB notification only."""
    try:
        n = Notification(user_id=user_id, title=title, body=body)
        db.session.add(n)
        db.session.commit()
    except Exception as e:
        logger.error(f'notify_user: {e}')


def notify_user_comms(user, title: str, body: str):
    """Create in-app notification + send WhatsApp + send email for a User object."""
    if user is None:
        return
    # In-app notification
    notify_user(user.id, title, body)
    # WhatsApp
    if getattr(user, 'phone', None):
        send_whatsapp(user.phone, f'{title}: {body}')
    # Email
    if getattr(user, 'email', None):
        send_email(user.email, f'BuildSmart — {title}', f'<p>{body}</p>')


def send_whatsapp(to_number: str, message: str) -> bool:
    """Send WhatsApp message via Twilio sandbox."""
    if not to_number:
        return False
    try:
        from twilio.rest import Client
        sid          = os.environ.get('TWILIO_ACCOUNT_SID')
        token        = os.environ.get('TWILIO_AUTH_TOKEN')
        from_number  = os.environ.get('TWILIO_WHATSAPP_FROM', 'whatsapp:+14155238886')
        if not sid or not token:
            logger.warning('Twilio credentials not configured — skipping WhatsApp send.')
            return False
        client = Client(sid, token)
        client.messages.create(
            from_=from_number,
            to=f'whatsapp:{to_number}',
            body=message
        )
        logger.info(f'WhatsApp sent to {to_number}')
        return True
    except ImportError:
        logger.warning('twilio package not installed — pip install twilio')
        return False
    except Exception as e:
        logger.error(f'Twilio WhatsApp error: {e}')
        return False


def send_email(to_email: str, subject: str, html_body: str) -> bool:
    """Send email via Resend."""
    if not to_email:
        return False
    try:
        import resend
        resend.api_key = os.environ.get('RESEND_API_KEY')
        if not resend.api_key:
            logger.warning('RESEND_API_KEY not configured — skipping email send.')
            return False
        resend.Emails.send({
            'from': os.environ.get('RESEND_FROM', 'BuildSmart <noreply@buildsmart.in>'),
            'to': [to_email],
            'subject': subject,
            'html': html_body
        })
        logger.info(f'Email sent to {to_email}: {subject}')
        return True
    except ImportError:
        logger.warning('resend package not installed — pip install resend')
        return False
    except Exception as e:
        logger.error(f'Resend email error: {e}')
        return False
