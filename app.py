from flask import Flask, render_template, send_from_directory, request, redirect, url_for, session, jsonify, abort
from flask_mail import Mail
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from functools import wraps
from models import db, Booking, PricingTier, Vehicle, Driver, AppSetting, Expense, MileageLog, DriverShift, Payout
from notifications import send_email_alert, send_sms_alert, send_rider_sms
from scheduler import check_availability, format_suggestions
from flight_tracker import check_flight, update_booking_flight_status, check_upcoming_flights, check_traffic_for_booking
from datetime import datetime, timedelta
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import os
import stripe

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-me')

# CSRF protection
csrf = CSRFProtect(app)

# Rate limiter
limiter = Limiter(get_remote_address, app=app, default_limits=["200 per day", "60 per hour"], storage_uri="memory://")

# Database configuration
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///gmcarservice.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

# Upload configuration
UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5 MB max


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Flask-Mail configuration
app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', '')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', '')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_USERNAME', '')
mail = Mail(app)

# Stripe configuration
stripe.api_key = os.environ.get('STRIPE_SECRET_KEY', '')
STRIPE_PUBLISHABLE_KEY = os.environ.get('STRIPE_PUBLISHABLE_KEY', '')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
ZELLE_BUSINESS_ID = os.environ.get('ZELLE_BUSINESS_ID', 'info@gmcarservice.com')  # Zelle email/phone

with app.app_context():
    db.create_all()
    # Seed default pricing tiers if table is empty
    if PricingTier.query.count() == 0:
        defaults = [
            # Sedan tiers
            PricingTier(vehicle_type='sedan', min_miles=0, max_miles=30, base_fare=15, per_mile=2.50, min_fare=15, label='0–30 mi', sort_order=0),
            PricingTier(vehicle_type='sedan', min_miles=30, max_miles=60, base_fare=15, per_mile=2.25, min_fare=15, label='30–60 mi', sort_order=1),
            PricingTier(vehicle_type='sedan', min_miles=60, max_miles=None, base_fare=15, per_mile=2.00, min_fare=15, label='60+ mi', sort_order=2),
            # SUV tiers
            PricingTier(vehicle_type='suv', min_miles=0, max_miles=30, base_fare=20, per_mile=3.00, min_fare=20, label='0–30 mi', sort_order=0),
            PricingTier(vehicle_type='suv', min_miles=30, max_miles=60, base_fare=20, per_mile=2.75, min_fare=20, label='30–60 mi', sort_order=1),
            PricingTier(vehicle_type='suv', min_miles=60, max_miles=None, base_fare=20, per_mile=2.50, min_fare=20, label='60+ mi', sort_order=2),
            # Luxury tiers
            PricingTier(vehicle_type='luxury', min_miles=0, max_miles=30, base_fare=30, per_mile=4.00, min_fare=30, label='0–30 mi', sort_order=0),
            PricingTier(vehicle_type='luxury', min_miles=30, max_miles=60, base_fare=30, per_mile=3.50, min_fare=30, label='30–60 mi', sort_order=1),
            PricingTier(vehicle_type='luxury', min_miles=60, max_miles=None, base_fare=30, per_mile=3.00, min_fare=30, label='60+ mi', sort_order=2),
        ]
        db.session.add_all(defaults)
        db.session.commit()

    # Seed default driver & vehicles if empty
    if Driver.query.count() == 0:
        owner = Driver(name='Gerry Jureidini', phone='', email='', is_active=True)
        db.session.add(owner)
        db.session.flush()  # get owner.id

        default_vehicles = [
            Vehicle(vehicle_type='sedan', name='Sedan', license_plate='', is_active=True, sort_order=0, driver_id=owner.id),
            Vehicle(vehicle_type='suv', name='SUV', license_plate='', is_active=False, sort_order=1),
            Vehicle(vehicle_type='luxury', name='Luxury', license_plate='', is_active=False, sort_order=2),
        ]
        db.session.add_all(default_vehicles)
        db.session.commit()


# --------------- Pricing Engine ---------------
VEHICLE_TYPES = ['sedan', 'suv', 'luxury']
VEHICLE_LABELS = {'sedan': 'Sedan', 'suv': 'SUV', 'luxury': 'Luxury'}


def get_tiers(vehicle_type):
    """Get pricing tiers for a vehicle type, ordered by min_miles."""
    return PricingTier.query.filter_by(vehicle_type=vehicle_type)\
        .order_by(PricingTier.min_miles.asc()).all()


def calculate_estimate(miles, vehicle_type='sedan'):
    """Calculate price using distance-based tiers from the database."""
    tiers = get_tiers(vehicle_type)
    if not tiers:
        return round(15 + 2.50 * miles, 2)  # fallback

    # Find the matching tier for this distance
    matched = tiers[0]  # default to first tier
    for tier in tiers:
        if tier.max_miles is None:
            if miles >= tier.min_miles:
                matched = tier
        else:
            if tier.min_miles <= miles < tier.max_miles:
                matched = tier
                break

    total = matched.base_fare + matched.per_mile * miles
    return round(max(total, matched.min_fare), 2)


# Google Maps API key — passed to templates
app.config['GOOGLE_MAPS_API_KEY'] = os.environ.get('GOOGLE_MAPS_API_KEY', '')


# --------------- SEO Route Data ---------------
SERVICE_CITIES = [
    {'slug': 'sarasota', 'name': 'Sarasota', 'lat': 27.3364, 'lng': -82.5307},
    {'slug': 'north-port', 'name': 'North Port', 'lat': 27.0442, 'lng': -82.2359},
    {'slug': 'venice', 'name': 'Venice', 'lat': 27.0998, 'lng': -82.4543},
    {'slug': 'osprey', 'name': 'Osprey', 'lat': 27.1948, 'lng': -82.4921},
    {'slug': 'nokomis', 'name': 'Nokomis', 'lat': 27.1192, 'lng': -82.4432},
    {'slug': 'englewood', 'name': 'Englewood', 'lat': 26.9620, 'lng': -82.3526},
    {'slug': 'siesta-key', 'name': 'Siesta Key', 'lat': 27.2678, 'lng': -82.5462},
    {'slug': 'longboat-key', 'name': 'Longboat Key', 'lat': 27.4036, 'lng': -82.6578},
    {'slug': 'lakewood-ranch', 'name': 'Lakewood Ranch', 'lat': 27.4012, 'lng': -82.3983},
]

SEO_ROUTES = [
    # Airports
    {'origin': 'sarasota', 'dest': 'tampa-airport-tpa', 'dest_name': 'Tampa Airport (TPA)', 'miles': 65, 'duration': '1 hr 15 min', 'price_sedan': 165, 'is_airport': True},
    {'origin': 'sarasota', 'dest': 'srq-airport', 'dest_name': 'SRQ Airport', 'miles': 8, 'duration': '15 min', 'price_sedan': 35, 'is_airport': True},
    {'origin': 'sarasota', 'dest': 'orlando-mco', 'dest_name': 'Orlando Airport (MCO)', 'miles': 140, 'duration': '2 hrs 15 min', 'price_sedan': 295, 'is_airport': True},
    {'origin': 'sarasota', 'dest': 'fort-lauderdale-fll', 'dest_name': 'Fort Lauderdale Airport (FLL)', 'miles': 230, 'duration': '3 hrs 30 min', 'price_sedan': 475, 'is_airport': True},
    {'origin': 'sarasota', 'dest': 'palm-beach-pbi', 'dest_name': 'Palm Beach Airport (PBI)', 'miles': 195, 'duration': '3 hrs', 'price_sedan': 405, 'is_airport': True},
    {'origin': 'sarasota', 'dest': 'miami-mia', 'dest_name': 'Miami Airport (MIA)', 'miles': 260, 'duration': '4 hrs', 'price_sedan': 535, 'is_airport': True},
    # Long distance cities
    {'origin': 'sarasota', 'dest': 'orlando', 'dest_name': 'Orlando', 'miles': 130, 'duration': '2 hrs', 'price_sedan': 275, 'is_airport': False},
    {'origin': 'sarasota', 'dest': 'fort-lauderdale', 'dest_name': 'Fort Lauderdale', 'miles': 230, 'duration': '3 hrs 30 min', 'price_sedan': 475, 'is_airport': False},
    {'origin': 'sarasota', 'dest': 'miami', 'dest_name': 'Miami', 'miles': 260, 'duration': '4 hrs', 'price_sedan': 535, 'is_airport': False},
    {'origin': 'sarasota', 'dest': 'palm-beach', 'dest_name': 'Palm Beach', 'miles': 195, 'duration': '3 hrs', 'price_sedan': 405, 'is_airport': False},
    # North Port origins
    {'origin': 'north-port', 'dest': 'tampa-airport-tpa', 'dest_name': 'Tampa Airport (TPA)', 'miles': 90, 'duration': '1 hr 40 min', 'price_sedan': 225, 'is_airport': True},
    {'origin': 'north-port', 'dest': 'srq-airport', 'dest_name': 'SRQ Airport', 'miles': 30, 'duration': '35 min', 'price_sedan': 90, 'is_airport': True},
    {'origin': 'north-port', 'dest': 'orlando', 'dest_name': 'Orlando', 'miles': 150, 'duration': '2 hrs 20 min', 'price_sedan': 315, 'is_airport': False},
    {'origin': 'north-port', 'dest': 'miami', 'dest_name': 'Miami', 'miles': 220, 'duration': '3 hrs 30 min', 'price_sedan': 455, 'is_airport': False},
    # Venice origins
    {'origin': 'venice', 'dest': 'tampa-airport-tpa', 'dest_name': 'Tampa Airport (TPA)', 'miles': 80, 'duration': '1 hr 30 min', 'price_sedan': 200, 'is_airport': True},
    {'origin': 'venice', 'dest': 'srq-airport', 'dest_name': 'SRQ Airport', 'miles': 20, 'duration': '25 min', 'price_sedan': 65, 'is_airport': True},
    {'origin': 'venice', 'dest': 'orlando', 'dest_name': 'Orlando', 'miles': 140, 'duration': '2 hrs 10 min', 'price_sedan': 295, 'is_airport': False},
    {'origin': 'venice', 'dest': 'fort-lauderdale', 'dest_name': 'Fort Lauderdale', 'miles': 210, 'duration': '3 hrs 15 min', 'price_sedan': 435, 'is_airport': False},
    # Siesta Key origins
    {'origin': 'siesta-key', 'dest': 'tampa-airport-tpa', 'dest_name': 'Tampa Airport (TPA)', 'miles': 75, 'duration': '1 hr 25 min', 'price_sedan': 185, 'is_airport': True},
    {'origin': 'siesta-key', 'dest': 'srq-airport', 'dest_name': 'SRQ Airport', 'miles': 12, 'duration': '20 min', 'price_sedan': 45, 'is_airport': True},
    # Englewood origins
    {'origin': 'englewood', 'dest': 'tampa-airport-tpa', 'dest_name': 'Tampa Airport (TPA)', 'miles': 100, 'duration': '1 hr 50 min', 'price_sedan': 250, 'is_airport': True},
    {'origin': 'englewood', 'dest': 'srq-airport', 'dest_name': 'SRQ Airport', 'miles': 35, 'duration': '40 min', 'price_sedan': 103, 'is_airport': True},
    {'origin': 'englewood', 'dest': 'fort-lauderdale', 'dest_name': 'Fort Lauderdale', 'miles': 195, 'duration': '3 hrs', 'price_sedan': 405, 'is_airport': False},
]

# Helper lookups
_CITY_BY_SLUG = {c['slug']: c for c in SERVICE_CITIES}
_ROUTE_INDEX = {}
for _r in SEO_ROUTES:
    _ROUTE_INDEX[(_r['origin'], _r['dest'])] = _r


def get_city(slug):
    return _CITY_BY_SLUG.get(slug)


def get_routes_for_city(slug):
    return [r for r in SEO_ROUTES if r['origin'] == slug]


# --------------- Routes ---------------
@app.route('/')
def index():
    # Recent completed rides for social proof (last 5, anonymized)
    recent_rides = Booking.query.filter_by(status='Completed').order_by(
        Booking.pickup_time.desc()
    ).limit(5).all()
    return render_template('index.html', service_cities=SERVICE_CITIES, seo_routes=SEO_ROUTES, recent_rides=recent_rides)


@app.route('/car-service-<origin>-to-<destination>')
def seo_route_page(origin, destination):
    route = _ROUTE_INDEX.get((origin, destination))
    if not route:
        return redirect(url_for('index')), 302
    origin_city = get_city(origin) or {'name': origin.replace('-', ' ').title(), 'slug': origin, 'lat': 27.3364, 'lng': -82.5307}
    # Compute SUV and luxury estimates
    price_suv = int(route['price_sedan'] * 1.25)
    price_luxury = int(route['price_sedan'] * 1.65)
    related = [r for r in SEO_ROUTES if r['origin'] == origin and r['dest'] != destination][:4]
    return render_template('seo_route.html',
        route=route, origin_city=origin_city,
        price_suv=price_suv, price_luxury=price_luxury,
        related_routes=related, service_cities=SERVICE_CITIES)


@app.route('/car-service-<city_slug>')
def seo_city_page(city_slug):
    city = get_city(city_slug)
    if not city:
        return redirect(url_for('index')), 302
    routes = get_routes_for_city(city_slug)
    return render_template('seo_city.html',
        city=city, routes=routes, service_cities=SERVICE_CITIES)


@app.route('/book', methods=['POST'])
def book():
    customer_name = request.form.get('customer_name', '').strip()
    phone = request.form.get('phone', '').strip()
    pickup_location = request.form.get('pickup_location', '').strip()
    dropoff_location = request.form.get('dropoff_location', '').strip()
    pickup_date_str = request.form.get('pickup_date', '').strip()
    pickup_time_str = request.form.get('pickup_time', '').strip()
    miles_str = request.form.get('miles', '0').strip()
    vehicle_type = request.form.get('vehicle_type', 'sedan').strip().lower()
    passengers_str = request.form.get('passengers', '1').strip()

    # Basic validation
    if not all([customer_name, phone, pickup_location, dropoff_location, pickup_date_str, pickup_time_str]):
        return render_template('index.html', error='All fields are required.'), 400

    if vehicle_type not in VEHICLE_TYPES:
        vehicle_type = 'sedan'

    try:
        pickup_time = datetime.fromisoformat(f'{pickup_date_str}T{pickup_time_str}')
    except ValueError:
        return render_template('index.html', error='Invalid date/time format.'), 400

    try:
        miles = max(float(miles_str), 0)
    except ValueError:
        miles = 0

    try:
        passengers = max(int(passengers_str), 1)
    except ValueError:
        passengers = 1

    # Toll fee from client-side route detection
    try:
        toll_fee = max(float(request.form.get('toll_fee', '0').strip()), 0)
        toll_fee = min(toll_fee, 50)  # cap at $50 to prevent abuse
    except ValueError:
        toll_fee = 0

    estimated_price = calculate_estimate(miles, vehicle_type) + toll_fee

    # --- Booking Agent: server-side availability check ---
    admin_override = request.form.get('admin_override') == '1' and session.get('admin_logged_in')
    trip_dur_str = request.form.get('trip_duration_min', '').strip()
    trip_duration_min = float(trip_dur_str) if trip_dur_str else None

    if not admin_override:
        avail = check_availability(
            pickup_location=pickup_location,
            dropoff_location=dropoff_location,
            requested_time=pickup_time,
            trip_duration_min=trip_duration_min,
            trip_distance_miles=miles if miles > 0 else None,
        )
        if not avail['available']:
            suggestions = format_suggestions(avail['suggested_times'])
            error_msg = avail['conflict_reason'] or 'This pickup time is not available.'
            if suggestions:
                error_msg += ' Available times: ' + ', '.join(suggestions)
            return render_template('index.html', error=error_msg), 409
        # Use scheduler's trip data if available
        if avail['trip_duration_min']:
            trip_duration_min = avail['trip_duration_min']
        if avail['trip_distance_miles'] and (not miles or miles <= 0):
            miles = avail['trip_distance_miles']

    booking = Booking(
        customer_name=customer_name,
        phone=phone,
        pickup_location=pickup_location,
        dropoff_location=dropoff_location,
        pickup_time=pickup_time,
        passengers=passengers,
        vehicle_type=vehicle_type,
        status='Pending',
        estimated_price=estimated_price,
        toll_fee=toll_fee if toll_fee > 0 else 0,
        trip_distance_miles=miles if miles > 0 else None,
        trip_duration_min=trip_duration_min,
        payment_method=request.form.get('payment_method', 'cash').strip().lower(),
        payment_status='unpaid',
    )

    # --- Passenger photo + airport pickup details ---
    pax_photo = request.files.get('passenger_photo')
    if pax_photo and pax_photo.filename and allowed_file(pax_photo.filename):
        fname = secure_filename(pax_photo.filename)
        fname = f"pax_{int(datetime.now().timestamp())}_{fname}"
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        pax_photo.save(os.path.join(UPLOAD_FOLDER, fname))
        booking.passenger_photo = fname

    is_airport = request.form.get('is_airport_pickup') == '1'
    booking.is_airport_pickup = is_airport
    if is_airport:
        booking.airport_exit = request.form.get('airport_exit', '').strip() or None
        booking.airport_door_color = request.form.get('airport_door_color', '').strip() or None
        booking.airport_door_number = request.form.get('airport_door_number', '').strip() or None
        booking.airport_notes = request.form.get('airport_notes', '').strip() or None
        booking.flight_number = request.form.get('flight_number', '').strip().upper() or None

    db.session.add(booking)
    db.session.commit()

    # Send notifications (fail silently so the booking still succeeds)
    try:
        send_email_alert(mail, booking)
    except Exception as e:
        app.logger.warning('Email notification failed: %s', e)

    try:
        send_sms_alert(booking)
    except Exception as e:
        app.logger.warning('SMS notification failed: %s', e)

    # --- Payment routing ---
    if booking.payment_method == 'stripe' and stripe.api_key:
        try:
            checkout_session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{
                    'price_data': {
                        'currency': 'usd',
                        'product_data': {
                            'name': f'G&M Car Service — Booking #{booking.id}',
                            'description': f'{booking.pickup_location.split(",")[0]} → {booking.dropoff_location.split(",")[0]} ({booking.vehicle_type.capitalize()})',
                        },
                        'unit_amount': int(round(booking.estimated_price * 100)),  # cents
                    },
                    'quantity': 1,
                }],
                mode='payment',
                success_url=request.host_url.rstrip('/') + url_for('payment_success', booking_id=booking.id) + '?session_id={CHECKOUT_SESSION_ID}',
                cancel_url=request.host_url.rstrip('/') + url_for('payment_cancel', booking_id=booking.id),
                metadata={'booking_id': str(booking.id)},
            )
            booking.stripe_session_id = checkout_session.id
            booking.payment_status = 'pending'
            db.session.commit()
            return redirect(checkout_session.url, code=303)
        except Exception as e:
            app.logger.warning('Stripe checkout failed: %s', e)
            # Fall through to receipt page — payment can be collected later

    return render_template('receipt.html', booking=booking, zelle_id=ZELLE_BUSINESS_ID)


# --------------- Admin Dashboard ---------------
ADMIN_SECRET = os.environ.get('ADMIN_SECRET', 'admin')
ADMIN_HASH = generate_password_hash(ADMIN_SECRET)


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated


@app.route('/admin/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def admin_login():
    if request.method == 'POST':
        if check_password_hash(ADMIN_HASH, request.form.get('secret', '')):
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        return render_template('admin_login.html', error='Invalid admin password.')
    return render_template('admin_login.html')


@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('index'))


@app.route('/admin')
@admin_required
def admin_dashboard():
    bookings = Booking.query.order_by(Booking.pickup_time.asc()).all()

    # Today's rides & earnings
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    today_rides = [b for b in bookings if b.pickup_time and today_start <= b.pickup_time < today_end]
    week_start = today_start - timedelta(days=now.weekday())  # Monday
    week_rides = [b for b in bookings if b.pickup_time and week_start <= b.pickup_time < today_end]

    today_earnings = sum(b.estimated_price for b in today_rides if b.status == 'Completed')
    week_earnings = sum(b.estimated_price for b in week_rides if b.status == 'Completed')
    total_earnings = sum(b.estimated_price for b in bookings if b.status == 'Completed')

    # Next upcoming ride
    upcoming = [b for b in bookings if b.pickup_time and b.pickup_time >= now and b.status not in ('Completed', 'No-Show', 'Cancelled')]
    next_ride = upcoming[0] if upcoming else None
    next_ride_min = int((next_ride.pickup_time - now).total_seconds() / 60) if next_ride else None

    return render_template('admin.html',
        bookings=bookings,
        today_rides=today_rides,
        today_earnings=today_earnings,
        week_earnings=week_earnings,
        total_earnings=total_earnings,
        next_ride=next_ride,
        next_ride_min=next_ride_min,
        now=now,
        drivers=Driver.query.filter_by(is_active=True).all(),
    )


@app.route('/admin/confirm/<int:booking_id>', methods=['POST'])
@admin_required
def admin_confirm(booking_id):
    booking = db.get_or_404(Booking, booking_id)
    booking.status = 'Confirmed'
    db.session.commit()
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/accept/<int:booking_id>', methods=['POST'])
@admin_required
def admin_accept(booking_id):
    booking = db.get_or_404(Booking, booking_id)
    eta = request.form.get('eta', '15 min').strip()
    booking.status = 'Accepted'
    booking.driver_eta = eta
    booking.accepted_at = datetime.utcnow()
    db.session.commit()

    # Send rider an SMS with ETA
    try:
        time_str = booking.pickup_time.strftime('%b %d at %I:%M %p')
        msg = (
            f"Hi {booking.customer_name}! Your G&M Car Service ride has been accepted.\n"
            f"Pickup: {booking.pickup_location}\n"
            f"Estimated arrival: {eta}\n"
            f"Vehicle: {booking.vehicle_type.capitalize()}\n"
            f"Scheduled: {time_str}\n"
            f"— G&M Car Service"
        )
        send_rider_sms(booking, msg)
        booking.last_notified = datetime.now()
        db.session.commit()
    except Exception as e:
        app.logger.warning('Rider SMS (accept) failed: %s', e)

    return redirect(url_for('admin_dashboard'))


@app.route('/admin/onmyway/<int:booking_id>', methods=['POST'])
@admin_required
def admin_onmyway(booking_id):
    booking = db.get_or_404(Booking, booking_id)
    booking.status = 'En Route'
    db.session.commit()

    # Send rider SMS with a Google Maps navigation link to track driver
    try:
        from urllib.parse import quote
        pickup_enc = quote(booking.pickup_location)
        maps_link = f"https://www.google.com/maps/dir/?api=1&destination={pickup_enc}&travelmode=driving"
        msg = (
            f"Hi {booking.customer_name}! Your driver is on the way to {booking.pickup_location}.\n"
            f"Track the route: {maps_link}\n"
            f"— G&M Car Service"
        )
        send_rider_sms(booking, msg)
        booking.last_notified = datetime.now()
        db.session.commit()
    except Exception as e:
        app.logger.warning('Rider SMS (on-my-way) failed: %s', e)

    return redirect(url_for('admin_dashboard'))


@app.route('/admin/arrived/<int:booking_id>', methods=['POST'])
@admin_required
def admin_arrived(booking_id):
    booking = db.get_or_404(Booking, booking_id)
    booking.status = 'Arrived'
    db.session.commit()

    try:
        msg = (
            f"Hi {booking.customer_name}! Your G&M Car Service driver has arrived at {booking.pickup_location}.\n"
            f"We're waiting for you!\n"
            f"— G&M Car Service"
        )
        send_rider_sms(booking, msg)
        booking.last_notified = datetime.now()
        db.session.commit()
    except Exception as e:
        app.logger.warning('Rider SMS (arrived) failed: %s', e)

    return redirect(url_for('admin_dashboard'))


@app.route('/admin/quick-sms/<int:booking_id>', methods=['POST'])
@admin_required
def admin_quick_sms(booking_id):
    booking = db.get_or_404(Booking, booking_id)
    data = request.get_json() if request.is_json else request.form
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({'error': 'Empty message'}), 400
    if len(message) > 500:
        message = message[:500]
    try:
        send_rider_sms(booking, message)
        booking.last_notified = datetime.now()
        db.session.commit()
        return jsonify({'success': True, 'message': 'SMS sent'})
    except Exception as e:
        app.logger.warning('Quick SMS failed for booking %s: %s', booking_id, e)
        return jsonify({'error': str(e)}), 500


@app.route('/admin/noshow/<int:booking_id>', methods=['POST'])
@admin_required
def admin_noshow(booking_id):
    booking = db.get_or_404(Booking, booking_id)
    booking.status = 'No-Show'
    db.session.commit()
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/complete/<int:booking_id>', methods=['POST'])
@admin_required
def admin_complete(booking_id):
    booking = db.get_or_404(Booking, booking_id)
    booking.status = 'Completed'

    # Auto-mark cash bookings as paid on completion (collected at pickup)
    if booking.payment_method == 'cash' and booking.payment_status != 'paid':
        booking.payment_status = 'paid'

    # --- Mileage Engine: auto-mint mileage record ---
    miles = booking.trip_distance_miles
    if miles and miles > 0:
        route_desc = f'{booking.pickup_location.split(",")[0]} → {booking.dropoff_location.split(",")[0]}'
        mileage = MileageLog(
            booking_id=booking.id,
            driver_id=booking.assigned_driver_id,
            date=booking.pickup_time.date() if booking.pickup_time else datetime.now().date(),
            miles=round(miles, 1),
            category='Business: Passenger Transport',
            route_description=route_desc,
            deduction_rate=0.725,
        )
        db.session.add(mileage)

    # --- Payout Engine: auto-mint driver payout ---
    if booking.assigned_driver_id:
        driver = db.session.get(Driver, booking.assigned_driver_id)
        if driver:
            fare = booking.estimated_price - (booking.toll_fee or 0)  # base fare minus tolls
            rate = driver.commission_rate or 0.60
            commission = round(fare * rate, 2)
            toll_reimburse = round(booking.toll_fee or 0, 2)
            tip = round(booking.tip or 0, 2)
            payout = Payout(
                driver_id=driver.id,
                booking_id=booking.id,
                date=booking.pickup_time.date() if booking.pickup_time else datetime.now().date(),
                fare_amount=round(fare, 2),
                commission_rate=rate,
                commission_amount=commission,
                toll_reimbursement=toll_reimburse,
                tip_amount=tip,
                total_payout=round(commission + toll_reimburse + tip, 2),
                status='Pending',
            )
            db.session.add(payout)

    db.session.commit()
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/delete/<int:booking_id>', methods=['POST'])
@admin_required
def admin_delete(booking_id):
    booking = db.get_or_404(Booking, booking_id)
    db.session.delete(booking)
    db.session.commit()
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/edit/<int:booking_id>', methods=['GET'])
@admin_required
def admin_edit_form(booking_id):
    booking = db.get_or_404(Booking, booking_id)
    return jsonify({
        'id': booking.id,
        'customer_name': booking.customer_name,
        'phone': booking.phone,
        'pickup_location': booking.pickup_location,
        'dropoff_location': booking.dropoff_location,
        'pickup_date': booking.pickup_time.strftime('%Y-%m-%d') if booking.pickup_time else '',
        'pickup_time': booking.pickup_time.strftime('%H:%M') if booking.pickup_time else '',
        'passengers': booking.passengers,
        'vehicle_type': booking.vehicle_type,
        'estimated_price': booking.estimated_price,
        'status': booking.status,
        'driver_notes': booking.driver_notes or '',
    })


@app.route('/admin/edit/<int:booking_id>', methods=['POST'])
@admin_required
def admin_edit_save(booking_id):
    booking = db.get_or_404(Booking, booking_id)
    data = request.get_json() if request.is_json else request.form

    if data.get('customer_name'):
        booking.customer_name = data['customer_name'].strip()
    if data.get('phone'):
        booking.phone = data['phone'].strip()
    if data.get('pickup_location'):
        booking.pickup_location = data['pickup_location'].strip()
    if data.get('dropoff_location'):
        booking.dropoff_location = data['dropoff_location'].strip()
    if data.get('pickup_date') and data.get('pickup_time'):
        try:
            booking.pickup_time = datetime.fromisoformat(f"{data['pickup_date']}T{data['pickup_time']}")
        except ValueError:
            pass
    if data.get('passengers'):
        try:
            booking.passengers = max(int(data['passengers']), 1)
        except (ValueError, TypeError):
            pass
    if data.get('vehicle_type') and data['vehicle_type'] in VEHICLE_TYPES:
        booking.vehicle_type = data['vehicle_type']
    if data.get('estimated_price'):
        try:
            booking.estimated_price = round(max(float(data['estimated_price']), 0), 2)
        except (ValueError, TypeError):
            pass
    if 'driver_notes' in data:
        booking.driver_notes = (data['driver_notes'] or '').strip()[:500] or None

    db.session.commit()

    if request.is_json:
        return jsonify({'success': True, 'id': booking.id})
    return redirect(url_for('admin_dashboard'))


# --------------- Admin: Pricing Management ---------------
@app.route('/admin/pricing')
@admin_required
def admin_pricing():
    tiers = PricingTier.query.order_by(
        PricingTier.vehicle_type, PricingTier.min_miles
    ).all()
    grouped = {}
    for vtype in VEHICLE_TYPES:
        grouped[vtype] = [t for t in tiers if t.vehicle_type == vtype]
    return render_template('admin_pricing.html', grouped=grouped,
                           vehicle_labels=VEHICLE_LABELS,
                           vehicle_types=VEHICLE_TYPES)


@app.route('/admin/pricing/save', methods=['POST'])
@admin_required
def admin_pricing_save():
    vehicle_type = request.form.get('vehicle_type', '').strip().lower()
    if vehicle_type not in VEHICLE_TYPES:
        return redirect(url_for('admin_pricing'))

    tier_id = request.form.get('tier_id', '').strip()
    min_miles = float(request.form.get('min_miles', 0))
    max_miles_str = request.form.get('max_miles', '').strip()
    max_miles = float(max_miles_str) if max_miles_str else None
    base_fare = float(request.form.get('base_fare', 0))
    per_mile = float(request.form.get('per_mile', 0))
    min_fare = float(request.form.get('min_fare', 0))
    label = request.form.get('label', '').strip()

    if tier_id:
        tier = db.get_or_404(PricingTier, int(tier_id))
        tier.min_miles = min_miles
        tier.max_miles = max_miles
        tier.base_fare = base_fare
        tier.per_mile = per_mile
        tier.min_fare = min_fare
        tier.label = label
    else:
        sort_order = PricingTier.query.filter_by(vehicle_type=vehicle_type).count()
        tier = PricingTier(
            vehicle_type=vehicle_type,
            min_miles=min_miles,
            max_miles=max_miles,
            base_fare=base_fare,
            per_mile=per_mile,
            min_fare=min_fare,
            label=label,
            sort_order=sort_order,
        )
        db.session.add(tier)

    db.session.commit()
    return redirect(url_for('admin_pricing'))


@app.route('/admin/pricing/delete/<int:tier_id>', methods=['POST'])
@admin_required
def admin_pricing_delete(tier_id):
    tier = db.get_or_404(PricingTier, tier_id)
    db.session.delete(tier)
    db.session.commit()
    return redirect(url_for('admin_pricing'))


# --------------- Admin: Fleet & Driver Management ---------------
@app.route('/admin/fleet')
@admin_required
def admin_fleet():
    vehicles = Vehicle.query.order_by(Vehicle.sort_order).all()
    drivers = Driver.query.order_by(Driver.name).all()
    return render_template('admin_fleet.html', vehicles=vehicles, drivers=drivers,
                           vehicle_types=VEHICLE_TYPES, vehicle_labels=VEHICLE_LABELS)


@app.route('/admin/fleet/save', methods=['POST'])
@admin_required
def admin_fleet_save():
    vehicle_id = request.form.get('vehicle_id', '').strip()
    vehicle_type = request.form.get('vehicle_type', '').strip().lower()
    name = request.form.get('name', '').strip()
    license_plate = request.form.get('license_plate', '').strip()
    is_active = request.form.get('is_active') == '1'
    driver_id = request.form.get('driver_id', '').strip()
    driver_id = int(driver_id) if driver_id else None

    if vehicle_type not in VEHICLE_TYPES:
        return redirect(url_for('admin_fleet'))

    # Handle photo upload
    photo_filename = None
    file = request.files.get('photo')
    if file and file.filename and allowed_file(file.filename):
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        fname = secure_filename(file.filename)
        # Prefix with timestamp to avoid collisions
        fname = f"{int(datetime.utcnow().timestamp())}_{fname}"
        file.save(os.path.join(UPLOAD_FOLDER, fname))
        photo_filename = fname

    if vehicle_id:
        vehicle = db.get_or_404(Vehicle, int(vehicle_id))
        vehicle.vehicle_type = vehicle_type
        vehicle.name = name
        vehicle.license_plate = license_plate
        vehicle.is_active = is_active
        vehicle.driver_id = driver_id
        if photo_filename:
            # Delete old photo if exists
            if vehicle.photo_filename:
                old_path = os.path.join(UPLOAD_FOLDER, vehicle.photo_filename)
                if os.path.exists(old_path):
                    os.remove(old_path)
            vehicle.photo_filename = photo_filename
    else:
        sort_order = Vehicle.query.count()
        vehicle = Vehicle(
            vehicle_type=vehicle_type,
            name=name,
            license_plate=license_plate,
            is_active=is_active,
            sort_order=sort_order,
            driver_id=driver_id,
            photo_filename=photo_filename,
        )
        db.session.add(vehicle)

    db.session.commit()
    return redirect(url_for('admin_fleet'))


@app.route('/admin/fleet/delete/<int:vehicle_id>', methods=['POST'])
@admin_required
def admin_fleet_delete(vehicle_id):
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    # Delete photo file
    if vehicle.photo_filename:
        photo_path = os.path.join(UPLOAD_FOLDER, vehicle.photo_filename)
        if os.path.exists(photo_path):
            os.remove(photo_path)
    db.session.delete(vehicle)
    db.session.commit()
    return redirect(url_for('admin_fleet'))


@app.route('/admin/driver/save', methods=['POST'])
@admin_required
def admin_driver_save():
    driver_id = request.form.get('driver_id', '').strip()
    name = request.form.get('name', '').strip()
    phone = request.form.get('phone', '').strip()
    email = request.form.get('email', '').strip()
    is_active = request.form.get('is_active') == '1'
    pin = request.form.get('pin', '').strip()
    zelle_info = request.form.get('zelle_info', '').strip()
    commission_str = request.form.get('commission_rate', '').strip()
    try:
        commission_rate = min(max(float(commission_str) / 100.0, 0), 1.0) if commission_str else 0.60
    except ValueError:
        commission_rate = 0.60

    # Handle driver photo upload
    photo_filename = None
    file = request.files.get('driver_photo')
    if file and file.filename and allowed_file(file.filename):
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        fname = secure_filename(file.filename)
        fname = f"driver_{int(datetime.utcnow().timestamp())}_{fname}"
        file.save(os.path.join(UPLOAD_FOLDER, fname))
        photo_filename = fname

    if driver_id:
        driver = db.get_or_404(Driver, int(driver_id))
        driver.name = name
        driver.phone = phone
        driver.email = email
        driver.is_active = is_active
        driver.pin = pin or driver.pin
        driver.zelle_info = zelle_info or driver.zelle_info
        driver.commission_rate = commission_rate
        if photo_filename:
            if driver.photo_filename:
                old_path = os.path.join(UPLOAD_FOLDER, driver.photo_filename)
                if os.path.exists(old_path):
                    os.remove(old_path)
            driver.photo_filename = photo_filename
    else:
        driver = Driver(name=name, phone=phone, email=email, is_active=is_active,
                        photo_filename=photo_filename, pin=pin or None,
                        zelle_info=zelle_info or None, commission_rate=commission_rate)
        db.session.add(driver)

    db.session.commit()
    return redirect(url_for('admin_fleet'))


@app.route('/admin/driver/delete/<int:driver_id>', methods=['POST'])
@admin_required
def admin_driver_delete(driver_id):
    driver = db.get_or_404(Driver, driver_id)
    # Delete driver photo
    if driver.photo_filename:
        photo_path = os.path.join(UPLOAD_FOLDER, driver.photo_filename)
        if os.path.exists(photo_path):
            os.remove(photo_path)
    # Unassign vehicles from this driver
    for v in driver.vehicles:
        v.driver_id = None
    db.session.delete(driver)
    db.session.commit()
    return redirect(url_for('admin_fleet'))


# --------------- Admin: Settings (API Keys) ---------------
@app.route('/admin/settings')
@admin_required
def admin_settings():
    settings = {
        'aviationstack_api_key': AppSetting.get('aviationstack_api_key', ''),
        'twilio_sid': AppSetting.get('twilio_sid', ''),
        'twilio_token': AppSetting.get('twilio_token', ''),
        'twilio_phone': AppSetting.get('twilio_phone', ''),
        'driver_phone': AppSetting.get('driver_phone', ''),
        'driver_home_address': AppSetting.get('driver_home_address', 'North Port, FL'),
    }
    return render_template('admin_settings.html', settings=settings)


@app.route('/admin/settings/save', methods=['POST'])
@admin_required
def admin_settings_save():
    keys = ['aviationstack_api_key', 'twilio_sid', 'twilio_token', 'twilio_phone',
            'driver_phone', 'driver_home_address']
    for key in keys:
        val = request.form.get(key, '').strip()
        AppSetting.set(key, val)
    return redirect(url_for('admin_settings'))


# --------------- Flight Tracking API ---------------
@app.route('/admin/flight/check/<int:booking_id>', methods=['POST'])
@admin_required
def admin_flight_check(booking_id):
    booking = db.get_or_404(Booking, booking_id)
    result = update_booking_flight_status(booking)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify(result)
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/flights/check-all', methods=['POST'])
@admin_required
def admin_flights_check_all():
    results = check_upcoming_flights(app)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'checked': len(results), 'results': results})
    return redirect(url_for('admin_dashboard'))


@app.route('/api/flight/<flight_number>')
def api_flight_status(flight_number):
    """Public endpoint — returns flight status JSON (for booking form preview)."""
    result = check_flight(flight_number)
    return jsonify(result)


@app.route('/admin/traffic/check/<int:booking_id>', methods=['POST'])
@admin_required
def admin_traffic_check(booking_id):
    """Manually trigger a traffic check for a specific booking."""
    booking = db.get_or_404(Booking, booking_id)
    result = check_traffic_for_booking(booking)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify(result or {'info': 'No traffic check needed (flight not within 2-hour window).'})
    return redirect(url_for('admin_dashboard'))


# --------------- Public API: Availability Check ---------------
@app.route('/api/check-availability', methods=['POST'])
@csrf.exempt
def api_check_availability():
    """
    Booking Agent: check if the requested pickup time is available.
    Expects JSON: { pickup, dropoff, datetime, trip_duration_min?, trip_distance_miles? }
    Returns: { available, conflict_reason, suggested_times[], trip_duration_min, trip_distance_miles }
    """
    data = request.get_json(silent=True) or {}
    pickup = data.get('pickup', '').strip()
    dropoff = data.get('dropoff', '').strip()
    dt_str = data.get('datetime', '').strip()
    trip_dur = data.get('trip_duration_min')
    trip_dist = data.get('trip_distance_miles')

    if not all([pickup, dropoff, dt_str]):
        return jsonify({'available': False, 'conflict_reason': 'Missing required fields.'}), 400

    try:
        requested_time = datetime.fromisoformat(dt_str)
    except ValueError:
        return jsonify({'available': False, 'conflict_reason': 'Invalid date/time format.'}), 400

    result = check_availability(
        pickup_location=pickup,
        dropoff_location=dropoff,
        requested_time=requested_time,
        trip_duration_min=trip_dur,
        trip_distance_miles=trip_dist,
    )

    return jsonify({
        'available': result['available'],
        'conflict_reason': result['conflict_reason'],
        'suggested_times': format_suggestions(result['suggested_times']),
        'trip_duration_min': result['trip_duration_min'],
        'trip_distance_miles': result['trip_distance_miles'],
    })


# --------------- Public API: Fleet for frontend ---------------
@app.route('/api/fleet')
def api_fleet():
    """Return all vehicles as JSON for the frontend."""
    vehicles = Vehicle.query.order_by(Vehicle.sort_order).all()
    result = []
    for v in vehicles:
        driver_info = None
        if v.driver:
            driver_info = {
                'name': v.driver.name,
                'photo_url': url_for('static', filename=f'uploads/{v.driver.photo_filename}') if v.driver.photo_filename else None,
            }
        result.append({
            'vehicle_type': v.vehicle_type,
            'name': v.name,
            'license_plate': v.license_plate,
            'photo_url': url_for('static', filename=f'uploads/{v.photo_filename}') if v.photo_filename else None,
            'is_active': v.is_active,
            'driver': driver_info,
        })
    return jsonify(result)


# --------------- Public API: Pricing for frontend ---------------
@app.route('/api/pricing')
def api_pricing():
    """Return all pricing tiers as JSON for the frontend quote calculator."""
    result = {}
    for vtype in VEHICLE_TYPES:
        tiers = get_tiers(vtype)
        result[vtype] = {
            'label': VEHICLE_LABELS.get(vtype, vtype.capitalize()),
            'tiers': [t.to_dict() for t in tiers],
        }
    return jsonify(result)


@app.route('/manifest.json')
def manifest():
    return send_from_directory('static', 'manifest.json')


@app.route('/sw.js')
def service_worker():
    return send_from_directory('static', 'sw.js')


@app.route('/sitemap.xml')
def sitemap():
    base = 'https://gmcarservice.com'
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'

    # Homepage
    xml += f'  <url><loc>{base}/</loc><changefreq>weekly</changefreq><priority>1.0</priority></url>\n'

    # City landing pages
    for city in SERVICE_CITIES:
        xml += f'  <url><loc>{base}/car-service-{city["slug"]}</loc><changefreq>monthly</changefreq><priority>0.8</priority></url>\n'

    # Route pages
    for r in SEO_ROUTES:
        xml += f'  <url><loc>{base}/car-service-{r["origin"]}-to-{r["dest"]}</loc><changefreq>monthly</changefreq><priority>0.7</priority></url>\n'

    xml += '</urlset>'
    return app.response_class(xml, mimetype='application/xml')


@app.route('/robots.txt')
def robots():
    txt = "User-agent: *\nAllow: /\n\nSitemap: https://gmcarservice.com/sitemap.xml\n"
    return app.response_class(txt, mimetype='text/plain')


# --------------- Social Proof API ---------------
@app.route('/api/recent-rides')
def api_recent_rides():
    """Public JSON feed of recent completed rides for social proof widget."""
    rides = Booking.query.filter_by(status='Completed').order_by(
        Booking.pickup_time.desc()
    ).limit(5).all()
    result = []
    for r in rides:
        # Anonymize: first name only + city-level locations
        first_name = r.customer_name.split(' ')[0] if r.customer_name else 'Rider'
        pickup_city = r.pickup_location.split(',')[0].strip() if r.pickup_location else ''
        dropoff_city = r.dropoff_location.split(',')[0].strip() if r.dropoff_location else ''
        result.append({
            'route': f'{pickup_city} → {dropoff_city}',
            'vehicle': r.vehicle_type.capitalize(),
            'date': r.pickup_time.strftime('%b %d') if r.pickup_time else '',
            'name': first_name,
        })
    return jsonify(result)


# --------------- Assign Driver to Booking ---------------
@app.route('/admin/assign-driver/<int:booking_id>', methods=['POST'])
@admin_required
def admin_assign_driver(booking_id):
    booking = db.get_or_404(Booking, booking_id)
    data = request.get_json() if request.is_json else request.form
    driver_id = data.get('driver_id')
    if driver_id:
        driver = db.session.get(Driver, int(driver_id))
        booking.assigned_driver_id = driver.id if driver else None
    else:
        booking.assigned_driver_id = None
    db.session.commit()
    if request.is_json:
        return jsonify({'success': True})
    return redirect(url_for('admin_dashboard'))


# --------------- Driver Portal (PIN login, sees only assigned rides) ---------------
@app.route('/driver/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def driver_login():
    if request.method == 'POST':
        pin = request.form.get('pin', '').strip()
        driver = Driver.query.filter_by(pin=pin, is_active=True).first() if pin else None
        if driver:
            session['driver_id'] = driver.id
            session['driver_name'] = driver.name
            return redirect(url_for('driver_portal'))
        return render_template('driver_login.html', error='Invalid PIN.')
    return render_template('driver_login.html')


@app.route('/driver')
def driver_portal():
    driver_id = session.get('driver_id')
    if not driver_id:
        return redirect(url_for('driver_login'))
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    my_rides = Booking.query.filter_by(assigned_driver_id=driver_id).filter(
        Booking.pickup_time >= today_start,
        Booking.pickup_time < today_end,
    ).order_by(Booking.pickup_time.asc()).all()
    return render_template('driver_portal.html',
        driver_name=session.get('driver_name', 'Driver'),
        rides=my_rides,
        now=now,
    )


@app.route('/driver/logout')
def driver_logout():
    session.pop('driver_id', None)
    session.pop('driver_name', None)
    return redirect(url_for('driver_login'))


# --------------- Odometer Snap ---------------
@app.route('/admin/odometer/<int:booking_id>', methods=['POST'])
@admin_required
def admin_odometer_upload(booking_id):
    """Upload an odometer photo and attach it to the booking's mileage log."""
    booking = db.get_or_404(Booking, booking_id)
    photo = request.files.get('odometer_photo')
    if not photo or not photo.filename or not allowed_file(photo.filename):
        return jsonify({'success': False, 'error': 'No valid photo'}), 400

    fname = secure_filename(photo.filename)
    fname = f"odo_{booking_id}_{int(datetime.now().timestamp())}_{fname}"
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    photo.save(os.path.join(UPLOAD_FOLDER, fname))

    # Attach to existing mileage log or create a stub
    mileage = MileageLog.query.filter_by(booking_id=booking_id).first()
    if mileage:
        mileage.odometer_photo = fname
    else:
        mileage = MileageLog(
            booking_id=booking_id,
            driver_id=booking.assigned_driver_id,
            date=booking.pickup_time.date() if booking.pickup_time else datetime.now().date(),
            miles=booking.trip_distance_miles or 0,
            category='Business: Passenger Transport',
            route_description=f'{booking.pickup_location.split(",")[0]} → {booking.dropoff_location.split(",")[0]}',
            odometer_photo=fname,
            deduction_rate=0.725,
        )
        db.session.add(mileage)

    db.session.commit()
    return jsonify({'success': True, 'filename': fname})


# --------------- Shift / Deadhead Tracking ---------------
@app.route('/driver/shift/start', methods=['POST'])
def driver_shift_start():
    driver_id = session.get('driver_id')
    if not driver_id:
        return jsonify({'success': False, 'error': 'Not logged in'}), 401

    # Close any existing active shift
    active = DriverShift.query.filter_by(driver_id=driver_id, is_active=True).first()
    if active:
        return jsonify({'success': False, 'error': 'Shift already active'}), 400

    data = request.get_json(silent=True) or {}
    shift = DriverShift(
        driver_id=driver_id,
        start_time=datetime.now(),
        start_location=data.get('location', ''),
        is_active=True,
    )
    db.session.add(shift)
    db.session.commit()
    return jsonify({'success': True, 'shift_id': shift.id})


@app.route('/driver/shift/end', methods=['POST'])
def driver_shift_end():
    driver_id = session.get('driver_id')
    if not driver_id:
        return jsonify({'success': False, 'error': 'Not logged in'}), 401

    shift = DriverShift.query.filter_by(driver_id=driver_id, is_active=True).first()
    if not shift:
        return jsonify({'success': False, 'error': 'No active shift'}), 400

    data = request.get_json(silent=True) or {}
    shift.end_time = datetime.now()
    shift.end_location = data.get('location', '')
    shift.is_active = False

    # Calculate positioning miles via Google Distance Matrix
    positioning_miles = 0
    if shift.start_location and shift.end_location:
        try:
            api_key = os.environ.get('GOOGLE_MAPS_API_KEY', '')
            import requests as req
            resp = req.get('https://maps.googleapis.com/maps/api/distancematrix/json', params={
                'origins': shift.start_location,
                'destinations': shift.end_location,
                'key': api_key,
                'units': 'imperial',
            }, timeout=10)
            dm = resp.json()
            if dm['status'] == 'OK':
                element = dm['rows'][0]['elements'][0]
                if element['status'] == 'OK':
                    meters = element['distance']['value']
                    positioning_miles = round(meters / 1609.34, 1)
        except Exception:
            pass

    shift.total_miles = positioning_miles
    db.session.commit()

    # Auto-create a positioning mileage log entry
    if positioning_miles > 0:
        mileage = MileageLog(
            driver_id=driver_id,
            date=datetime.now().date(),
            miles=positioning_miles,
            category='Business: Positioning / Deadhead',
            route_description=f'{shift.start_location.split(",")[0] if shift.start_location else "Start"} → {shift.end_location.split(",")[0] if shift.end_location else "End"}',
            deduction_rate=0.725,
        )
        db.session.add(mileage)
        db.session.commit()

    return jsonify({
        'success': True,
        'miles': positioning_miles,
        'deduction': round(positioning_miles * 0.725, 2),
    })


@app.route('/driver/shift/status')
def driver_shift_status():
    driver_id = session.get('driver_id')
    if not driver_id:
        return jsonify({'active': False})
    shift = DriverShift.query.filter_by(driver_id=driver_id, is_active=True).first()
    return jsonify({
        'active': bool(shift),
        'shift_id': shift.id if shift else None,
        'start_time': shift.start_time.isoformat() if shift else None,
    })


# --------------- Expense Tracking ---------------
EXPENSE_CATEGORIES = ['Fuel', 'Tolls', 'Maintenance', 'Car Wash', 'Insurance', 'Driver Payout', 'Other']


@app.route('/admin/expenses')
@admin_required
def admin_expenses():
    expenses = Expense.query.order_by(Expense.date.desc()).all()
    mileage_logs = MileageLog.query.order_by(MileageLog.date.desc()).all()
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    year_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)

    month_total = sum(e.amount for e in expenses if e.date and e.date >= month_start.date())
    year_total = sum(e.amount for e in expenses if e.date and e.date >= year_start.date())

    # Income for P&L
    year_income = sum(b.estimated_price for b in Booking.query.filter_by(status='Completed').all()
                      if b.pickup_time and b.pickup_time >= year_start)

    # Category totals for the year
    cat_totals = {}
    for cat in EXPENSE_CATEGORIES:
        cat_totals[cat] = sum(e.amount for e in expenses if e.category == cat and e.date and e.date >= year_start.date())

    # --- Mileage / Tax Deduction Stats ---
    year_logs = [m for m in mileage_logs if m.date and m.date >= year_start.date()]
    total_business_miles = sum(m.miles for m in year_logs)
    mileage_deduction = round(total_business_miles * 0.725, 2)
    toll_deductions = cat_totals.get('Tolls', 0)
    total_projected_writeoff = round(mileage_deduction + toll_deductions, 2)

    return render_template('admin_expenses.html',
        expenses=expenses,
        mileage_logs=year_logs,
        categories=EXPENSE_CATEGORIES,
        month_total=month_total,
        year_total=year_total,
        year_income=year_income,
        cat_totals=cat_totals,
        total_business_miles=total_business_miles,
        mileage_deduction=mileage_deduction,
        toll_deductions=toll_deductions,
        total_projected_writeoff=total_projected_writeoff,
        # Payout data
        drivers=Driver.query.filter_by(is_active=True).order_by(Driver.name).all(),
        payouts=Payout.query.order_by(Payout.date.desc()).all(),
        year_payouts_total=sum(p.total_payout for p in Payout.query.all()
                               if p.date and p.date >= year_start.date()),
        pending_payouts_total=sum(p.total_payout for p in Payout.query.filter_by(status='Pending').all()),
    )


@app.route('/admin/expenses/add', methods=['POST'])
@admin_required
def admin_expense_add():
    date_str = request.form.get('date', '').strip()
    category = request.form.get('category', '').strip()
    amount_str = request.form.get('amount', '').strip()
    description = request.form.get('description', '').strip()

    if not date_str or not amount_str or category not in EXPENSE_CATEGORIES:
        return redirect(url_for('admin_expenses'))

    expense = Expense(
        date=datetime.fromisoformat(date_str).date(),
        category=category,
        amount=round(float(amount_str), 2),
        description=description or None,
    )

    receipt = request.files.get('receipt')
    if receipt and receipt.filename and allowed_file(receipt.filename):
        fname = secure_filename(receipt.filename)
        fname = f"receipt_{int(datetime.now().timestamp())}_{fname}"
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        receipt.save(os.path.join(UPLOAD_FOLDER, fname))
        expense.receipt_filename = fname

    db.session.add(expense)
    db.session.commit()
    return redirect(url_for('admin_expenses'))


@app.route('/admin/expenses/delete/<int:expense_id>', methods=['POST'])
@admin_required
def admin_expense_delete(expense_id):
    expense = db.get_or_404(Expense, expense_id)
    db.session.delete(expense)
    db.session.commit()
    return redirect(url_for('admin_expenses'))


# --------------- Payout Management ---------------
@app.route('/admin/payout/mark-paid/<int:payout_id>', methods=['POST'])
@csrf.exempt
@admin_required
def admin_payout_mark_paid(payout_id):
    """Mark a payout as settled and auto-log it as an expense."""
    payout = db.get_or_404(Payout, payout_id)
    data = request.get_json(silent=True) or {}
    payout.status = 'Settled'
    payout.paid_at = datetime.now()
    payout.payment_method = data.get('method', 'Zelle')

    # Auto-log as expense for the LLC books
    driver = db.session.get(Driver, payout.driver_id)
    driver_name = driver.name if driver else 'Driver'
    expense = Expense(
        date=datetime.now().date(),
        category='Driver Payout',
        amount=payout.total_payout,
        description=f'Payout to {driver_name} — Booking #{payout.booking_id or "N/A"}',
        driver_id=payout.driver_id,
    )
    db.session.add(expense)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/admin/payout/zelle-info/<int:payout_id>')
@admin_required
def admin_payout_zelle_info(payout_id):
    """Return Zelle info + amount for clipboard copy."""
    payout = db.get_or_404(Payout, payout_id)
    driver = db.session.get(Driver, payout.driver_id)
    return jsonify({
        'driver_name': driver.name if driver else 'Unknown',
        'zelle_info': driver.zelle_info if driver else '',
        'amount': payout.total_payout,
        'memo': f'G&M Payout #{payout.id}',
    })


@app.route('/admin/expenses/export')
@admin_required
def admin_expenses_export():
    """Export all expenses, income, and mileage as CSV for CPA."""
    import csv
    import io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Date', 'Type', 'Category', 'Description', 'Amount', 'Miles', 'Deduction @0.725'])

    # Expenses
    for e in Expense.query.order_by(Expense.date.asc()).all():
        writer.writerow([
            e.date.isoformat() if e.date else '',
            'Expense',
            e.category,
            e.description or '',
            f'-{e.amount:.2f}',
            '',
            '',
        ])

    # Income (completed bookings)
    for b in Booking.query.filter_by(status='Completed').order_by(Booking.pickup_time.asc()).all():
        route = f'{b.pickup_location.split(",")[0]} → {b.dropoff_location.split(",")[0]}'
        writer.writerow([
            b.pickup_time.strftime('%Y-%m-%d') if b.pickup_time else '',
            'Income',
            'Ride Fare',
            route,
            f'{b.estimated_price:.2f}',
            f'{b.trip_distance_miles:.1f}' if b.trip_distance_miles else '',
            '',
        ])

    # Mileage deductions
    for m in MileageLog.query.order_by(MileageLog.date.asc()).all():
        writer.writerow([
            m.date.isoformat() if m.date else '',
            'Mileage Deduction',
            m.category,
            m.route_description or '',
            '',
            f'{m.miles:.1f}',
            f'{m.deduction_value:.2f}',
        ])

    csv_data = output.getvalue()
    return app.response_class(
        csv_data,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment;filename=gm_carservice_ledger_{datetime.now().strftime("%Y%m%d")}.csv'}
    )


# --------------- Error Handlers ---------------
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def server_error(e):
    return render_template('500.html'), 500


# --------------- Legal Pages ---------------
@app.route('/privacy')
def privacy_policy():
    return render_template('privacy.html')

@app.route('/terms')
def terms_of_service():
    return render_template('terms.html')

@app.route('/disclaimer')
def disclaimer():
    return render_template('disclaimer.html')

@app.route('/contact', methods=['GET', 'POST'])
@limiter.limit("10 per hour")
def contact():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        phone = request.form.get('phone', '').strip()
        message = request.form.get('message', '').strip()
        if not name or not message:
            return render_template('contact.html', error='Name and message are required.')
        try:
            business_email = os.environ.get('BUSINESS_EMAIL', os.environ.get('MAIL_USERNAME', ''))
            if business_email:
                from flask_mail import Message
                msg = Message(
                    subject=f'[GM Car Service] Contact from {name}',
                    recipients=[business_email],
                    reply_to=email or None,
                    body=f"Name: {name}\nEmail: {email}\nPhone: {phone}\n\n{message}"
                )
                mail.send(msg)
            return render_template('contact.html', success=True)
        except Exception:
            return render_template('contact.html', error='Could not send message. Please call us instead.')
    return render_template('contact.html')


# --------------- Payment Routes ---------------
@app.route('/payment/success/<int:booking_id>')
def payment_success(booking_id):
    """Stripe redirects here after successful payment."""
    booking = db.get_or_404(Booking, booking_id)
    session_id = request.args.get('session_id', '')
    # Verify the Stripe session if possible
    if stripe.api_key and session_id:
        try:
            sess = stripe.checkout.Session.retrieve(session_id)
            if sess.payment_status == 'paid' and sess.metadata.get('booking_id') == str(booking_id):
                booking.payment_status = 'paid'
                db.session.commit()
        except Exception as e:
            app.logger.warning('Stripe session verify failed: %s', e)
    return render_template('receipt.html', booking=booking, zelle_id=ZELLE_BUSINESS_ID, payment_success=True)


@app.route('/payment/cancel/<int:booking_id>')
def payment_cancel(booking_id):
    """Stripe redirects here if customer cancels payment."""
    booking = db.get_or_404(Booking, booking_id)
    return render_template('receipt.html', booking=booking, zelle_id=ZELLE_BUSINESS_ID, payment_cancelled=True)


@app.route('/webhook/stripe', methods=['POST'])
@csrf.exempt
@limiter.exempt
def stripe_webhook():
    """Stripe webhook for payment confirmation (backup to redirect)."""
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature', '')

    if STRIPE_WEBHOOK_SECRET:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        except (ValueError, stripe.error.SignatureVerificationError):
            return jsonify({'error': 'Invalid signature'}), 400
    else:
        try:
            event = stripe.Event.construct_from(
                stripe.util.json.loads(payload), stripe.api_key
            )
        except Exception:
            return jsonify({'error': 'Invalid payload'}), 400

    if event['type'] == 'checkout.session.completed':
        session_data = event['data']['object']
        booking_id = session_data.get('metadata', {}).get('booking_id')
        if booking_id:
            booking = db.session.get(Booking, int(booking_id))
            if booking:
                booking.payment_status = 'paid'
                booking.stripe_session_id = session_data.get('id', booking.stripe_session_id)
                db.session.commit()

    return jsonify({'status': 'ok'}), 200


@app.route('/admin/payment/mark-paid/<int:booking_id>', methods=['POST'])
@csrf.exempt
@admin_required
def admin_mark_payment(booking_id):
    """Admin manually marks a booking as paid (for Cash/Zelle collected offline)."""
    booking = db.get_or_404(Booking, booking_id)
    data = request.get_json(silent=True) or {}
    booking.payment_status = 'paid'
    method = data.get('method', booking.payment_method)
    if method in ('cash', 'zelle', 'stripe'):
        booking.payment_method = method
    db.session.commit()
    return jsonify({'success': True})


if __name__ == '__main__':
    app.run(debug=True, port=5000)
