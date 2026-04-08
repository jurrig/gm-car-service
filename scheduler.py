"""
Booking Scheduler / Availability Agent
=======================================
Prevents double-bookings by computing driver busy windows and checking
whether a requested pickup time is feasible given:
  - 15-min early arrival buffer at pickup
  - Estimated trip duration (pickup → dropoff)
  - 10-min post-dropoff buffer (passenger exit, reset)
  - Deadhead drive time (previous dropoff → next pickup)

Uses Google Distance Matrix API for travel time estimates, with a
~35 mph fallback if the API is unavailable.
"""

import os
import requests
from datetime import datetime, timedelta
from models import Booking

# --------------- Config ---------------
EARLY_ARRIVAL_MIN = 15   # driver should arrive 15 min before pickup
POST_DROPOFF_MIN = 10    # buffer after dropoff
FALLBACK_MPH = 35        # fallback speed if Google API fails
SUGGEST_WINDOW_HOURS = 6 # look +/- this many hours for available slots
SLOT_INCREMENT_MIN = 15  # suggest times in 15-min increments
MIN_BOOKING_NOTICE_MIN = 60  # must book at least 1 hour ahead


def get_google_api_key():
    return os.environ.get('GOOGLE_MAPS_API_KEY', '')


def get_drive_time_minutes(origin, destination):
    """
    Get driving time in minutes between two addresses using Google Distance Matrix API.
    Returns (duration_minutes, distance_miles) or falls back to estimate.
    """
    api_key = get_google_api_key()
    if not api_key or not origin or not destination:
        return None, None

    try:
        resp = requests.get(
            'https://maps.googleapis.com/maps/api/distancematrix/json',
            params={
                'origins': origin,
                'destinations': destination,
                'mode': 'driving',
                'units': 'imperial',
                'key': api_key,
            },
            timeout=10,
        )
        data = resp.json()
        if data.get('status') != 'OK':
            return None, None

        element = data['rows'][0]['elements'][0]
        if element.get('status') != 'OK':
            return None, None

        duration_min = element['duration']['value'] / 60.0  # seconds → minutes
        distance_miles = element['distance']['value'] / 1609.34  # meters → miles
        return round(duration_min, 1), round(distance_miles, 1)
    except Exception:
        return None, None


def estimate_drive_time_fallback(miles):
    """Estimate drive time at 35 mph average."""
    if not miles or miles <= 0:
        return 20  # minimum 20 min fallback
    return max(miles / FALLBACK_MPH * 60, 10)


def get_active_bookings_for_date(date_obj):
    """
    Get all bookings for a given date that are NOT cancelled/completed/no-show.
    These represent the driver's committed schedule.
    """
    day_start = datetime.combine(date_obj, datetime.min.time())
    day_end = day_start + timedelta(days=1)

    active_statuses = ['Pending', 'Confirmed', 'Accepted', 'En Route', 'Arrived']
    return Booking.query.filter(
        Booking.pickup_time >= day_start,
        Booking.pickup_time < day_end,
        Booking.status.in_(active_statuses),
    ).order_by(Booking.pickup_time.asc()).all()


def compute_busy_window(booking, next_pickup_location=None):
    """
    Compute the time window during which the driver is busy for a given booking.

    Returns (busy_start, busy_end):
      busy_start = pickup_time - EARLY_ARRIVAL_MIN - deadhead_to_pickup
      busy_end   = pickup_time + trip_duration + POST_DROPOFF_MIN

    If next_pickup_location is provided, we also add the deadhead time from
    this booking's dropoff to the next pickup.
    """
    trip_duration = booking.trip_duration_min or 0
    if trip_duration <= 0:
        # Estimate from distance
        if booking.trip_distance_miles and booking.trip_distance_miles > 0:
            trip_duration = estimate_drive_time_fallback(booking.trip_distance_miles)
        else:
            trip_duration = 30  # default 30 min if no data

    busy_start = booking.pickup_time - timedelta(minutes=EARLY_ARRIVAL_MIN)
    busy_end = booking.pickup_time + timedelta(minutes=trip_duration + POST_DROPOFF_MIN)

    return busy_start, busy_end


def check_availability(pickup_location, dropoff_location, requested_time,
                       trip_duration_min=None, trip_distance_miles=None,
                       exclude_booking_id=None):
    """
    Check if the requested pickup time is available for the single driver.

    Returns a dict:
    {
        'available': True/False,
        'conflict_reason': str or None,
        'suggested_times': [list of datetime] if not available,
        'trip_duration_min': float,
        'trip_distance_miles': float,
    }
    """
    result = {
        'available': False,
        'conflict_reason': None,
        'suggested_times': [],
        'trip_duration_min': trip_duration_min,
        'trip_distance_miles': trip_distance_miles,
    }

    # --- Get trip info if not provided ---
    if trip_duration_min is None or trip_distance_miles is None:
        dur, dist = get_drive_time_minutes(pickup_location, dropoff_location)
        if dur is not None:
            result['trip_duration_min'] = dur
            result['trip_distance_miles'] = dist
        else:
            # Use distance if available, else fallback
            if trip_distance_miles and trip_distance_miles > 0:
                result['trip_duration_min'] = estimate_drive_time_fallback(trip_distance_miles)
            else:
                result['trip_duration_min'] = 30
                result['trip_distance_miles'] = 0

    trip_dur = result['trip_duration_min'] or 30

    # --- Minimum notice check ---
    now = datetime.utcnow()
    if requested_time < now + timedelta(minutes=MIN_BOOKING_NOTICE_MIN):
        result['conflict_reason'] = f'Bookings require at least {MIN_BOOKING_NOTICE_MIN} minutes advance notice.'
        result['suggested_times'] = _find_available_slots(
            requested_time.date(), requested_time, trip_dur,
            pickup_location, dropoff_location, exclude_booking_id
        )
        return result

    # --- Get all existing bookings for the day ---
    bookings = get_active_bookings_for_date(requested_time.date())
    if exclude_booking_id:
        bookings = [b for b in bookings if b.id != exclude_booking_id]

    if not bookings:
        result['available'] = True
        return result

    # --- Build the new booking's busy window ---
    new_busy_start = requested_time - timedelta(minutes=EARLY_ARRIVAL_MIN)
    new_busy_end = requested_time + timedelta(minutes=trip_dur + POST_DROPOFF_MIN)

    # --- Check against each existing booking ---
    for booking in bookings:
        existing_start, existing_end = compute_busy_window(booking)

        # Check if the driver can get from existing dropoff to new pickup in time
        deadhead_min = _get_deadhead_time(booking.dropoff_location, pickup_location)

        # The driver is free after existing_end + deadhead to new pickup
        driver_available_at = existing_end + timedelta(minutes=deadhead_min)

        # The driver needs to leave for new pickup at:
        driver_must_leave_by = requested_time - timedelta(minutes=EARLY_ARRIVAL_MIN)

        # Also check reverse: can the driver get from new dropoff to existing pickup?
        # (if the new booking is BEFORE an existing one)
        deadhead_to_existing = _get_deadhead_time(dropoff_location, booking.pickup_location)
        new_trip_done = requested_time + timedelta(minutes=trip_dur + POST_DROPOFF_MIN)
        must_be_at_existing = booking.pickup_time - timedelta(minutes=EARLY_ARRIVAL_MIN)

        # Overlap check: two windows overlap if one starts before the other ends
        # Window 1: [new_busy_start, new_busy_end]
        # Window 2: [existing_start, existing_end]
        direct_overlap = (new_busy_start < existing_end) and (existing_start < new_busy_end)

        # Deadhead conflict: driver can't get from one to the other in time
        # Case A: existing booking finishes, then new booking
        if existing_end <= requested_time:
            if driver_available_at + timedelta(minutes=deadhead_min) > driver_must_leave_by:
                if driver_available_at > driver_must_leave_by:
                    time_str = booking.pickup_time.strftime('%I:%M %p')
                    result['conflict_reason'] = (
                        f'Driver has a booking at {time_str} and won\'t be available in time '
                        f'for your pickup (needs ~{int(deadhead_min)} min to reach you after that trip).'
                    )
                    result['suggested_times'] = _find_available_slots(
                        requested_time.date(), requested_time, trip_dur,
                        pickup_location, dropoff_location, exclude_booking_id
                    )
                    return result

        # Case B: new booking finishes, then existing booking
        if new_busy_end <= existing_start:
            if new_trip_done + timedelta(minutes=deadhead_to_existing) > must_be_at_existing:
                time_str = booking.pickup_time.strftime('%I:%M %p')
                result['conflict_reason'] = (
                    f'Driver has a booking at {time_str}. Your trip would finish too late '
                    f'for the driver to reach that next pickup.'
                )
                result['suggested_times'] = _find_available_slots(
                    requested_time.date(), requested_time, trip_dur,
                    pickup_location, dropoff_location, exclude_booking_id
                )
                return result

        # Direct time overlap
        if direct_overlap:
            time_str = booking.pickup_time.strftime('%I:%M %p')
            result['conflict_reason'] = (
                f'Driver is already booked at {time_str}. This time overlaps with that trip.'
            )
            result['suggested_times'] = _find_available_slots(
                requested_time.date(), requested_time, trip_dur,
                pickup_location, dropoff_location, exclude_booking_id
            )
            return result

    # All checks passed
    result['available'] = True
    return result


def _get_deadhead_time(from_location, to_location):
    """Get travel time from one location to another (deadhead drive)."""
    dur, _ = get_drive_time_minutes(from_location, to_location)
    if dur is not None:
        return dur
    # Fallback: assume 25 min average deadhead
    return 25


def _find_available_slots(date_obj, around_time, trip_dur_min,
                          pickup_location, dropoff_location,
                          exclude_booking_id=None):
    """
    Find up to 4 available time slots near the requested time.
    Searches +/- SUGGEST_WINDOW_HOURS in SLOT_INCREMENT_MIN increments.
    """
    bookings = get_active_bookings_for_date(date_obj)
    if exclude_booking_id:
        bookings = [b for b in bookings if b.id != exclude_booking_id]

    suggestions = []
    now = datetime.utcnow() + timedelta(minutes=MIN_BOOKING_NOTICE_MIN)

    # Generate candidate times: before and after the requested time
    candidates = []
    for offset in range(0, SUGGEST_WINDOW_HOURS * 60 // SLOT_INCREMENT_MIN + 1):
        minutes = offset * SLOT_INCREMENT_MIN
        if minutes == 0:
            continue
        t_after = around_time + timedelta(minutes=minutes)
        t_before = around_time - timedelta(minutes=minutes)
        if t_after.date() == date_obj:
            candidates.append(t_after)
        if t_before.date() == date_obj and t_before > now:
            candidates.append(t_before)

    # Sort by proximity to requested time
    candidates.sort(key=lambda t: abs((t - around_time).total_seconds()))

    for candidate in candidates:
        if candidate < now:
            continue
        # Quick overlap check (no Google calls for suggestions — use fallback)
        new_start = candidate - timedelta(minutes=EARLY_ARRIVAL_MIN)
        new_end = candidate + timedelta(minutes=trip_dur_min + POST_DROPOFF_MIN)

        conflict = False
        for booking in bookings:
            b_start, b_end = compute_busy_window(booking)
            # Add generous deadhead buffer for suggestions (no Google API calls here)
            buffer = timedelta(minutes=60)  # conservative fallback deadhead
            if (new_start < b_end + buffer) and (b_start - buffer < new_end):
                conflict = True
                break

        if not conflict:
            suggestions.append(candidate)
            if len(suggestions) >= 4:
                break

    return suggestions


def format_suggestions(suggestions):
    """Format datetime suggestions for display."""
    return [t.strftime('%I:%M %p') for t in suggestions]
