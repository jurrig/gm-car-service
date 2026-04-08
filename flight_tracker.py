"""
Flight Tracker — AviationStack API integration + traffic-aware departure alerts.
Checks flight status for airport pickup bookings, auto-adjusts pickup times,
and monitors real-time traffic to the airport via Google Distance Matrix API.
"""
import os
import requests
from datetime import datetime, timedelta
from models import db, Booking, AppSetting
import logging

logger = logging.getLogger(__name__)

AVIATIONSTACK_BASE = 'http://api.aviationstack.com/v1/flights'

# Traffic alert config
DRIVER_HOME = 'North Port, FL'                 # Driver's departure point
TRAFFIC_CHECK_HOURS_BEFORE = 2                  # Check traffic this many hours before landing
TRAFFIC_ALERT_THRESHOLD_MIN = 75                # Alert if drive time exceeds this (1 hr 15 min)
NORMAL_DRIVE_MIN = 60                           # Normal drive time without traffic (~1 hr)


def get_api_key():
    """Retrieve the AviationStack API key from DB settings."""
    return AppSetting.get('aviationstack_api_key')


def parse_flight_number(flight_num):
    """Split 'AA1234' into airline IATA code 'AA' and flight number '1234'."""
    flight_num = (flight_num or '').strip().upper().replace(' ', '')
    if len(flight_num) < 3:
        return None, None
    # Airline code is 2 chars (sometimes 3), rest is number
    for i in range(2, min(4, len(flight_num))):
        if flight_num[i:].isdigit():
            return flight_num[:i], flight_num[i:]
    return None, None


def check_flight(flight_number):
    """
    Query AviationStack for a flight's status.
    Returns dict with status info or None on failure.
    """
    api_key = get_api_key()
    if not api_key:
        return {'error': 'No AviationStack API key configured. Add it in Admin → Settings.'}

    airline, number = parse_flight_number(flight_number)
    if not airline or not number:
        return {'error': f'Invalid flight number format: {flight_number}. Use format like AA1234.'}

    try:
        resp = requests.get(AVIATIONSTACK_BASE, params={
            'access_key': api_key,
            'flight_iata': f'{airline}{number}',
            'limit': 1,
        }, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.warning('AviationStack API error: %s', e)
        return {'error': f'Flight API request failed: {e}'}

    if data.get('error'):
        err_info = data['error'].get('message', 'API error')
        return {'error': err_info}

    flights = data.get('data', [])
    if not flights:
        return {'error': f'No flight data found for {flight_number}.'}

    flight = flights[0]

    # Extract useful fields
    arrival = flight.get('arrival', {})
    departure = flight.get('departure', {})
    status = flight.get('flight_status', 'unknown')

    result = {
        'flight_number': flight_number.upper(),
        'airline': flight.get('airline', {}).get('name', ''),
        'status': status,  # scheduled, active, landed, cancelled, incident, diverted
        'departure_airport': departure.get('airport', ''),
        'departure_iata': departure.get('iata', ''),
        'departure_scheduled': departure.get('scheduled', ''),
        'departure_actual': departure.get('actual', ''),
        'arrival_airport': arrival.get('airport', ''),
        'arrival_iata': arrival.get('iata', ''),
        'arrival_scheduled': arrival.get('scheduled', ''),
        'arrival_estimated': arrival.get('estimated', ''),
        'arrival_actual': arrival.get('actual', ''),
        'arrival_gate': arrival.get('gate', ''),
        'arrival_terminal': arrival.get('terminal', ''),
        'arrival_baggage': arrival.get('baggage', ''),
        'delay_minutes': arrival.get('delay') or 0,
    }

    # Calculate the best arrival time to use for pickup scheduling
    for time_field in ['arrival_actual', 'arrival_estimated', 'arrival_scheduled']:
        if result[time_field]:
            try:
                result['best_arrival_time'] = datetime.fromisoformat(
                    result[time_field].replace('Z', '+00:00').replace('+00:00', '')
                )
                break
            except (ValueError, TypeError):
                continue

    return result


def update_booking_flight_status(booking):
    """
    Check flight status for a booking and update its fields.
    Returns the flight data dict or error dict.
    """
    if not booking.flight_number:
        return {'error': 'No flight number on this booking.'}

    flight_data = check_flight(booking.flight_number)
    if 'error' in flight_data:
        return flight_data

    # Update booking fields
    booking.flight_status = flight_data['status']

    best_time = flight_data.get('best_arrival_time')
    if best_time:
        booking.flight_arrival = best_time

        # Auto-adjust pickup time if flight is delayed
        delay = flight_data.get('delay_minutes', 0)
        if delay and delay > 10:
            # Add 30 min buffer after landing for baggage/customs
            new_pickup = best_time + timedelta(minutes=30)
            old_pickup = booking.pickup_time
            if new_pickup > old_pickup:
                booking.pickup_time = new_pickup
                flight_data['pickup_adjusted'] = True
                flight_data['old_pickup'] = old_pickup.strftime('%I:%M %p')
                flight_data['new_pickup'] = new_pickup.strftime('%I:%M %p')

    db.session.commit()

    # After updating flight status, check traffic if within window
    traffic = check_traffic_for_booking(booking)
    if traffic and 'error' not in traffic:
        flight_data['traffic'] = traffic

    return flight_data


def check_traffic_to_airport(origin, destination_airport):
    """
    Check real-time traffic from origin to an airport using Google Distance Matrix API
    with departure_time=now for traffic-aware estimates.

    Returns dict with duration_min, duration_in_traffic_min, distance_miles, or error.
    """
    api_key = os.environ.get('GOOGLE_MAPS_API_KEY', '')
    if not api_key:
        return {'error': 'No Google Maps API key configured.'}

    try:
        resp = requests.get(
            'https://maps.googleapis.com/maps/api/distancematrix/json',
            params={
                'origins': origin,
                'destinations': destination_airport,
                'mode': 'driving',
                'departure_time': 'now',
                'traffic_model': 'best_guess',
                'units': 'imperial',
                'key': api_key,
            },
            timeout=10,
        )
        data = resp.json()
        if data.get('status') != 'OK':
            return {'error': f'Distance Matrix API error: {data.get("status")}'}

        element = data['rows'][0]['elements'][0]
        if element.get('status') != 'OK':
            return {'error': f'Route error: {element.get("status")}'}

        result = {
            'duration_min': round(element['duration']['value'] / 60.0, 1),
            'distance_miles': round(element['distance']['value'] / 1609.34, 1),
            'origin': origin,
            'destination': destination_airport,
        }

        # duration_in_traffic is only available when departure_time=now
        if 'duration_in_traffic' in element:
            result['duration_in_traffic_min'] = round(
                element['duration_in_traffic']['value'] / 60.0, 1
            )
            result['duration_in_traffic_text'] = element['duration_in_traffic']['text']
        else:
            result['duration_in_traffic_min'] = result['duration_min']
            result['duration_in_traffic_text'] = element['duration']['text']

        result['exceeds_threshold'] = (
            result['duration_in_traffic_min'] > TRAFFIC_ALERT_THRESHOLD_MIN
        )

        return result

    except Exception as e:
        logger.warning('Traffic check failed: %s', e)
        return {'error': f'Traffic check failed: {e}'}


def send_leave_early_alert(booking, traffic_data):
    """Send SMS + in-app alert to driver when traffic is heavy."""
    from notifications import send_sms_alert
    extra_min = int(traffic_data['duration_in_traffic_min'] - NORMAL_DRIVE_MIN)
    drive_text = traffic_data.get('duration_in_traffic_text', f"{int(traffic_data['duration_in_traffic_min'])} min")
    flight_eta = booking.flight_arrival.strftime('%I:%M %p') if booking.flight_arrival else 'unknown'

    # Build alert message
    msg = (
        f"🚨 LEAVE EARLY ALERT — Booking #{booking.id}\n"
        f"Passenger: {booking.customer_name}\n"
        f"Flight {booking.flight_number} lands at {flight_eta}\n"
        f"Current drive to airport: {drive_text} (normally ~{NORMAL_DRIVE_MIN} min)\n"
        f"Extra {extra_min} min due to traffic (likely Skyway congestion)\n"
        f"Leave NOW or adjust your departure.\n"
        f"— G&M Car Service Agent"
    )

    # Try to send SMS to driver's phone (from env or DB)
    driver_phone = os.environ.get('DRIVER_PHONE', '') or AppSetting.get('driver_phone', '')
    if driver_phone:
        try:
            account_sid = os.environ.get('TWILIO_ACCOUNT_SID', '') or AppSetting.get('twilio_sid', '')
            auth_token = os.environ.get('TWILIO_AUTH_TOKEN', '') or AppSetting.get('twilio_token', '')
            from_number = os.environ.get('TWILIO_FROM_NUMBER', '') or AppSetting.get('twilio_phone', '')

            if all([account_sid, auth_token, from_number]):
                from twilio.rest import Client
                client = Client(account_sid, auth_token)
                client.messages.create(body=msg, from_=from_number, to=driver_phone)
                logger.info('Leave Early SMS sent to %s for booking #%d', driver_phone, booking.id)
        except Exception as e:
            logger.warning('Leave Early SMS failed: %s', e)

    return msg


def check_traffic_for_booking(booking):
    """
    If a booking's flight lands within TRAFFIC_CHECK_HOURS_BEFORE hours,
    check real-time traffic and trigger alert if needed.
    Returns traffic data dict or None.
    """
    if not booking.flight_arrival or not booking.is_airport_pickup:
        return None

    now = datetime.utcnow()
    landing_time = booking.flight_arrival
    hours_until_landing = (landing_time - now).total_seconds() / 3600.0

    # Only check when flight is within the check window AND hasn't landed yet
    if hours_until_landing < 0 or hours_until_landing > TRAFFIC_CHECK_HOURS_BEFORE:
        return None

    # Determine the airport destination
    pickup = (booking.pickup_location or '').strip()
    # Use the pickup location as the airport destination
    # (for airport pickups, pickup_location IS the airport)
    airport = pickup if pickup else 'Tampa International Airport, FL'

    # Use driver's configured home or default to North Port
    driver_origin = AppSetting.get('driver_home_address', DRIVER_HOME)

    traffic = check_traffic_to_airport(driver_origin, airport)
    if 'error' in traffic:
        return traffic

    # Save traffic duration to booking
    booking.traffic_duration_min = traffic.get('duration_in_traffic_min')

    # Send "Leave Early" alert if threshold exceeded and not already sent
    if traffic['exceeds_threshold'] and not booking.traffic_alert_sent:
        traffic['alert_message'] = send_leave_early_alert(booking, traffic)
        booking.traffic_alert_sent = True
        traffic['alert_triggered'] = True
    else:
        traffic['alert_triggered'] = False

    db.session.commit()
    return traffic


def check_upcoming_flights(app):
    """
    Check all airport bookings with flight numbers that are upcoming (within 4 hours).
    Called periodically or on-demand from admin.
    """
    results = []
    now = datetime.utcnow()
    window = now + timedelta(hours=4)

    with app.app_context():
        bookings = Booking.query.filter(
            Booking.is_airport_pickup == True,
            Booking.flight_number.isnot(None),
            Booking.flight_number != '',
            Booking.status.in_(['Pending', 'Confirmed', 'Accepted']),
            Booking.pickup_time <= window,
            Booking.pickup_time >= now - timedelta(hours=2),
        ).all()

        for booking in bookings:
            result = update_booking_flight_status(booking)
            result['booking_id'] = booking.id
            result['customer_name'] = booking.customer_name
            results.append(result)

    return results
