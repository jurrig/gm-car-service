const express = require('express');
const db = require('../models/database');
const { authenticate } = require('../middleware/auth');

const router = express.Router();
router.use(authenticate);

// List vehicles (optionally filter by customer)
router.get('/', (req, res) => {
    const { customer_id } = req.query;
    let sql = `SELECT v.*, c.first_name || ' ' || c.last_name AS owner_name FROM vehicles v JOIN customers c ON v.customer_id = c.id`;
    const params = [];

    if (customer_id) {
        sql += ' WHERE v.customer_id = ?';
        params.push(customer_id);
    }

    sql += ' ORDER BY v.created_at DESC';
    res.json(db.prepare(sql).all(...params));
});

// Get single vehicle
router.get('/:id', (req, res) => {
    const vehicle = db.prepare(`
    SELECT v.*, c.first_name || ' ' || c.last_name AS owner_name
    FROM vehicles v JOIN customers c ON v.customer_id = c.id WHERE v.id = ?
  `).get(req.params.id);

    if (!vehicle) return res.status(404).json({ error: 'Vehicle not found' });

    const jobs = db.prepare('SELECT * FROM service_jobs WHERE vehicle_id = ? ORDER BY created_at DESC').all(req.params.id);
    res.json({ ...vehicle, service_history: jobs });
});

// Create vehicle
router.post('/', (req, res) => {
    const { customer_id, make, model, year, vin, license_plate, color, mileage } = req.body;
    if (!customer_id || !make || !model || !year) {
        return res.status(400).json({ error: 'customer_id, make, model, and year are required' });
    }

    const result = db.prepare(`
    INSERT INTO vehicles (customer_id, make, model, year, vin, license_plate, color, mileage)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
  `).run(customer_id, make, model, year, vin || null, license_plate || null, color || null, mileage || null);

    res.status(201).json(db.prepare('SELECT * FROM vehicles WHERE id = ?').get(result.lastInsertRowid));
});

// Update vehicle
router.put('/:id', (req, res) => {
    const existing = db.prepare('SELECT * FROM vehicles WHERE id = ?').get(req.params.id);
    if (!existing) return res.status(404).json({ error: 'Vehicle not found' });

    const { make, model, year, vin, license_plate, color, mileage } = req.body;
    db.prepare('UPDATE vehicles SET make=?, model=?, year=?, vin=?, license_plate=?, color=?, mileage=? WHERE id=?')
        .run(make || existing.make, model || existing.model, year || existing.year, vin !== undefined ? vin : existing.vin, license_plate !== undefined ? license_plate : existing.license_plate, color !== undefined ? color : existing.color, mileage !== undefined ? mileage : existing.mileage, req.params.id);

    res.json(db.prepare('SELECT * FROM vehicles WHERE id = ?').get(req.params.id));
});

module.exports = router;
