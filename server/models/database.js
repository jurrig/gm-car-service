const Database = require('better-sqlite3');
const path = require('path');

const db = new Database(path.join(__dirname, '..', 'gmcarservice.db'));

// Enable WAL mode for better concurrency
db.pragma('journal_mode = WAL');
db.pragma('foreign_keys = ON');

// Create tables
db.exec(`
  CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'technician' CHECK(role IN ('admin', 'technician', 'receptionist')),
    full_name TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT,
    phone TEXT NOT NULL,
    address TEXT,
    created_at TEXT DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS vehicles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    make TEXT NOT NULL,
    model TEXT NOT NULL,
    year INTEGER NOT NULL,
    vin TEXT,
    license_plate TEXT,
    color TEXT,
    mileage INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS service_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    assigned_to INTEGER REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'in_progress', 'waiting_parts', 'completed', 'cancelled')),
    title TEXT NOT NULL,
    description TEXT,
    estimated_cost REAL,
    final_cost REAL,
    mileage_in INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT
  );

  CREATE TABLE IF NOT EXISTS job_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES service_jobs(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    note TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
  );

  CREATE INDEX IF NOT EXISTS idx_jobs_status ON service_jobs(status);
  CREATE INDEX IF NOT EXISTS idx_jobs_customer ON service_jobs(customer_id);
  CREATE INDEX IF NOT EXISTS idx_vehicles_customer ON vehicles(customer_id);
`);

module.exports = db;
