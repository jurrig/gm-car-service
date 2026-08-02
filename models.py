from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import secrets

db = SQLAlchemy()


def generate_token():
    return secrets.token_urlsafe(16)


class Booking(db.Model):
    __tablename__ = 'bookings'

    id = db.Column(db.Integer, primary_key=True)
    customer_name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(120), nullable=True)
    pickup_location = db.Column(db.String(255), nullable=False)
    dropoff_location = db.Column(db.String(255), nullable=False)
    pickup_time = db.Column(db.DateTime, nullable=False)
    passengers = db.Column(db.Integer, default=1)
    vehicle_type = db.Column(db.String(20), nullable=False, default='sedan')
    status = db.Column(db.String(20), nullable=False, default='Pending')
    estimated_price = db.Column(db.Float, nullable=False)
    trip_distance_miles = db.Column(db.Float, nullable=True)
    trip_duration_min = db.Column(db.Float, nullable=True)
    driver_eta = db.Column(db.String(50), nullable=True)
    accepted_at = db.Column(db.DateTime, nullable=True)
    passenger_photo = db.Column(db.String(255), nullable=True)
    is_airport_pickup = db.Column(db.Boolean, default=False)
    airport_exit = db.Column(db.String(100), nullable=True)
    airport_door_color = db.Column(db.String(50), nullable=True)
    airport_door_number = db.Column(db.String(50), nullable=True)
    airport_notes = db.Column(db.String(500), nullable=True)
    flight_number = db.Column(db.String(20), nullable=True)
    flight_status = db.Column(db.String(30), nullable=True)
    flight_arrival = db.Column(db.DateTime, nullable=True)
    traffic_duration_min = db.Column(db.Float, nullable=True)
    traffic_alert_sent = db.Column(db.Boolean, default=False)
    driver_notes = db.Column(db.Text, nullable=True)
    last_notified = db.Column(db.DateTime, nullable=True)
    assigned_driver_id = db.Column(db.Integer, db.ForeignKey('drivers.id'), nullable=True)
    toll_fee = db.Column(db.Float, nullable=True, default=0)
    tip = db.Column(db.Float, nullable=True, default=0)
    payment_method = db.Column(db.String(20), nullable=False, default='cash')
    payment_status = db.Column(db.String(20), nullable=False, default='unpaid')
    payment_token = db.Column(db.String(32), nullable=True, default=generate_token)
    stripe_session_id = db.Column(db.String(255), nullable=True)
    driver_lat = db.Column(db.Float, nullable=True)
    driver_lng = db.Column(db.Float, nullable=True)
    driver_location_updated = db.Column(db.DateTime, nullable=True)
    pickup_lat = db.Column(db.Float, nullable=True)
    pickup_lng = db.Column(db.Float, nullable=True)
    discount_percent = db.Column(db.Float, nullable=True, default=0)
    discount_reason = db.Column(db.String(100), nullable=True)
    promo_code = db.Column(db.String(50), nullable=True)
    original_price = db.Column(db.Float, nullable=True)
    is_flat_rate = db.Column(db.Boolean, default=False)
    flat_rate_id = db.Column(db.Integer, nullable=True)
    concierge_access_code = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    assigned_driver = db.relationship('Driver', backref='bookings', foreign_keys=[assigned_driver_id])

    def __repr__(self):
        return f'<Booking {self.id} - {self.customer_name}>'


class Article(db.Model):
    __tablename__ = 'articles'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(180), nullable=False)
    slug = db.Column(db.String(180), nullable=False, unique=True, index=True)
    content = db.Column(db.Text, nullable=False)
    meta_description = db.Column(db.String(255), nullable=True)
    published_at = db.Column(db.DateTime, nullable=True, index=True)


class ArticleFeedback(db.Model):
    __tablename__ = 'article_feedback'

    id = db.Column(db.Integer, primary_key=True)
    article_id = db.Column(db.Integer, nullable=False, index=True)
    vote = db.Column(db.String(8), nullable=False, index=True)
    source_path = db.Column(db.String(255), nullable=True)
    visitor_hash = db.Column(db.String(64), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)


class PricingTier(db.Model):
    __tablename__ = 'pricing_tiers'

    id = db.Column(db.Integer, primary_key=True)
    vehicle_type = db.Column(db.String(20), nullable=False)  # sedan, suv, luxury
    min_miles = db.Column(db.Float, nullable=False, default=0)
    max_miles = db.Column(db.Float, nullable=True)  # NULL = unlimited
    base_fare = db.Column(db.Float, nullable=False, default=0)
    per_mile = db.Column(db.Float, nullable=False)
    min_fare = db.Column(db.Float, nullable=False, default=0)
    label = db.Column(db.String(50), nullable=True)  # e.g. "0–30 mi"
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    def __repr__(self):
        max_str = str(self.max_miles) if self.max_miles else '∞'
        return f'<PricingTier {self.vehicle_type} {self.min_miles}-{max_str}mi @${self.per_mile}/mi>'

    def to_dict(self):
        return {
            'id': self.id,
            'vehicle_type': self.vehicle_type,
            'min_miles': self.min_miles,
            'max_miles': self.max_miles,
            'base_fare': self.base_fare,
            'per_mile': self.per_mile,
            'min_fare': self.min_fare,
            'label': self.label,
        }


class Vehicle(db.Model):
    __tablename__ = 'vehicles'

    id = db.Column(db.Integer, primary_key=True)
    vehicle_type = db.Column(db.String(20), nullable=False)  # sedan, suv, luxury
    name = db.Column(db.String(100), nullable=False)  # e.g. "2023 Lincoln Town Car"
    license_plate = db.Column(db.String(20), nullable=False, default='')
    photo_filename = db.Column(db.String(255), nullable=True)  # uploaded file name
    is_active = db.Column(db.Boolean, nullable=False, default=True)  # False = "Coming Soon"
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    driver_id = db.Column(db.Integer, db.ForeignKey('drivers.id'), nullable=True)

    driver = db.relationship('Driver', backref='vehicles')

    def __repr__(self):
        return f'<Vehicle {self.id} {self.name} ({self.vehicle_type})>'


class Driver(db.Model):
    __tablename__ = 'drivers'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20), nullable=False, default='')
    email = db.Column(db.String(120), nullable=True)
    photo_filename = db.Column(db.String(255), nullable=True)
    pin = db.Column(db.String(10), nullable=True)                 # simple PIN for driver login
    commission_rate = db.Column(db.Float, nullable=False, default=0.60)  # 0.60 = 60% to driver
    zelle_info = db.Column(db.String(120), nullable=True)          # email or phone for Zelle payments
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Driver {self.id} {self.name} @{int(self.commission_rate*100)}%>'


class MedicalOffice(db.Model):
    __tablename__ = 'medical_offices'

    id = db.Column(db.Integer, primary_key=True)
    access_code = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    address = db.Column(db.String(255), nullable=False)
    office_id_key = db.Column(db.String(50), unique=True, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Expense(db.Model):
    __tablename__ = 'expenses'

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    category = db.Column(db.String(50), nullable=False)           # Fuel, Tolls, Maintenance, Car Wash, Insurance, Other
    amount = db.Column(db.Float, nullable=False)
    description = db.Column(db.String(255), nullable=True)
    receipt_filename = db.Column(db.String(255), nullable=True)   # uploaded receipt photo
    driver_id = db.Column(db.Integer, db.ForeignKey('drivers.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    driver = db.relationship('Driver', backref='expenses')

    def __repr__(self):
        return f'<Expense {self.id} {self.category} ${self.amount}>'


class Payout(db.Model):
    __tablename__ = 'payouts'

    id = db.Column(db.Integer, primary_key=True)
    driver_id = db.Column(db.Integer, db.ForeignKey('drivers.id'), nullable=False)
    booking_id = db.Column(db.Integer, db.ForeignKey('bookings.id'), nullable=True)
    date = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    fare_amount = db.Column(db.Float, nullable=False, default=0)        # base fare for this ride
    commission_rate = db.Column(db.Float, nullable=False, default=0.60) # rate at time of payout
    commission_amount = db.Column(db.Float, nullable=False, default=0)  # fare * rate
    toll_reimbursement = db.Column(db.Float, nullable=False, default=0) # 100% pass-through
    tip_amount = db.Column(db.Float, nullable=False, default=0)         # 100% to driver
    total_payout = db.Column(db.Float, nullable=False, default=0)       # commission + tolls + tips
    status = db.Column(db.String(20), nullable=False, default='Pending') # Pending / Settled
    paid_at = db.Column(db.DateTime, nullable=True)
    payment_method = db.Column(db.String(50), nullable=True)            # Zelle, Cash, Check
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    driver = db.relationship('Driver', backref='payouts')
    booking = db.relationship('Booking', backref='payout', uselist=False)

    def __repr__(self):
        return f'<Payout {self.id} ${self.total_payout} to driver {self.driver_id}>'


class MileageLog(db.Model):
    __tablename__ = 'mileage_logs'

    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey('bookings.id'), nullable=True)
    driver_id = db.Column(db.Integer, db.ForeignKey('drivers.id'), nullable=True)
    date = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    miles = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(60), nullable=False, default='Business: Passenger Transport')
    route_description = db.Column(db.String(255), nullable=True)
    odometer_photo = db.Column(db.String(255), nullable=True)
    deduction_rate = db.Column(db.Float, nullable=False, default=0.725)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    booking = db.relationship('Booking', backref='mileage_log', uselist=False)
    driver = db.relationship('Driver', backref='mileage_logs')

    @property
    def deduction_value(self):
        return round(self.miles * self.deduction_rate, 2)

    def __repr__(self):
        return f'<MileageLog {self.id} {self.miles}mi ${self.deduction_value}>'


class DriverShift(db.Model):
    __tablename__ = 'driver_shifts'

    id = db.Column(db.Integer, primary_key=True)
    driver_id = db.Column(db.Integer, db.ForeignKey('drivers.id'), nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=True)
    start_location = db.Column(db.String(255), nullable=True)  # GPS or address
    end_location = db.Column(db.String(255), nullable=True)
    total_miles = db.Column(db.Float, nullable=True)           # calculated positioning miles
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    driver = db.relationship('Driver', backref='shifts')

    def __repr__(self):
        return f'<DriverShift {self.id} driver={self.driver_id}>'


class AppSetting(db.Model):
    __tablename__ = 'app_settings'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), nullable=False, unique=True)
    value = db.Column(db.Text, nullable=True)

    @staticmethod
    def get(key, default=None):
        row = AppSetting.query.filter_by(key=key).first()
        return row.value if row and row.value else default

    @staticmethod
    def set(key, value):
        row = AppSetting.query.filter_by(key=key).first()
        if row:
            row.value = value
        else:
            row = AppSetting(key=key, value=value)
            db.session.add(row)
        db.session.commit()


class PromoCode(db.Model):
    __tablename__ = 'promo_codes'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), nullable=False, unique=True)
    discount_percent = db.Column(db.Float, nullable=False, default=10)
    max_uses = db.Column(db.Integer, nullable=True)  # NULL = unlimited
    current_uses = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    expires_at = db.Column(db.DateTime, nullable=True)  # NULL = never expires
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def is_valid(self):
        if not self.is_active:
            return False
        if self.max_uses and self.current_uses >= self.max_uses:
            return False
        if self.expires_at and datetime.utcnow() > self.expires_at:
            return False
        return True

    def __repr__(self):
        return f'<PromoCode {self.code} {self.discount_percent}%>'


class FlatRate(db.Model):
    __tablename__ = 'flat_rates'

    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(120), nullable=False)  # e.g. "Sarasota → TPA"
    origin_keywords = db.Column(db.String(255), nullable=False)  # comma-separated: "sarasota,srq,siesta"
    dest_keywords = db.Column(db.String(255), nullable=False)    # comma-separated: "tampa airport,tpa"
    price_sedan = db.Column(db.Float, nullable=False, default=0)
    price_suv = db.Column(db.Float, nullable=True)
    price_luxury = db.Column(db.Float, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    is_bidirectional = db.Column(db.Boolean, nullable=False, default=True)  # match reverse too
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def get_price(self, vehicle_type='sedan'):
        if vehicle_type == 'suv' and self.price_suv:
            return self.price_suv
        if vehicle_type == 'luxury' and self.price_luxury:
            return self.price_luxury
        return self.price_sedan

    def matches(self, pickup, dropoff):
        """Check if pickup/dropoff text matches this flat rate's keywords."""
        pickup_lower = pickup.lower()
        dropoff_lower = dropoff.lower()
        origin_kws = [k.strip() for k in self.origin_keywords.lower().split(',') if k.strip()]
        dest_kws = [k.strip() for k in self.dest_keywords.lower().split(',') if k.strip()]

        fwd = any(k in pickup_lower for k in origin_kws) and any(k in dropoff_lower for k in dest_kws)
        if fwd:
            return True
        if self.is_bidirectional:
            rev = any(k in pickup_lower for k in dest_kws) and any(k in dropoff_lower for k in origin_kws)
            return rev
        return False

    def __repr__(self):
        return f'<FlatRate {self.id} {self.label} ${self.price_sedan}>'
