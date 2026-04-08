from flask_mail import Message
from twilio.rest import Client
import os


def send_email_alert(mail, booking):
    """Send an email notification to the business when a new booking arrives."""
    recipient = os.environ.get('BUSINESS_EMAIL', '')
    if not recipient:
        return

    subject = f'New Booking: {booking.customer_name} – #{booking.id}'
    body = (
        f"New ride booking received!\n\n"
        f"Booking ID : #{booking.id}\n"
        f"Customer   : {booking.customer_name}\n"
        f"Phone      : {booking.phone}\n"
        f"Pickup     : {booking.pickup_location}\n"
        f"Drop-off   : {booking.dropoff_location}\n"
        f"Pickup Time: {booking.pickup_time.strftime('%B %d, %Y at %I:%M %p')}\n"
        f"Estimate   : ${booking.estimated_price:.2f}\n"
        f"Status     : {booking.status}\n\n"
        f"— G&M Car Service System"
    )

    msg = Message(
        subject=subject,
        recipients=[recipient],
        body=body,
    )
    mail.send(msg)


def send_sms_alert(booking):
    """Send an SMS notification via Twilio when a new booking arrives."""
    account_sid = os.environ.get('TWILIO_ACCOUNT_SID', '')
    auth_token = os.environ.get('TWILIO_AUTH_TOKEN', '')
    from_number = os.environ.get('TWILIO_FROM_NUMBER', '')
    to_number = os.environ.get('TWILIO_TO_NUMBER', '')

    if not all([account_sid, auth_token, from_number, to_number]):
        return

    client = Client(account_sid, auth_token)
    time_str = booking.pickup_time.strftime('%b %d at %I:%M %p')

    client.messages.create(
        body=f'New Booking: {booking.customer_name} at {time_str}. Check the dashboard.',
        from_=from_number,
        to=to_number,
    )


def send_rider_sms(booking, message):
    """Send an SMS to the rider's phone number."""
    account_sid = os.environ.get('TWILIO_ACCOUNT_SID', '')
    auth_token = os.environ.get('TWILIO_AUTH_TOKEN', '')
    from_number = os.environ.get('TWILIO_FROM_NUMBER', '')

    if not all([account_sid, auth_token, from_number, booking.phone]):
        return False

    client = Client(account_sid, auth_token)
    client.messages.create(
        body=message,
        from_=from_number,
        to=booking.phone,
    )
    return True
