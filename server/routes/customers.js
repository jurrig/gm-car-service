const express = require('express');
const db = require('../models/database');
const { authenticate } = require('../middleware/auth');

const router = express.Router();
router.use(authenticate);

// List customers
router.get('/', (req, res) => {
    const { search } = req.query;
    let sql = 'SELECT * FROM customers';
    const params = [];

    if (search) {
        sql += " WHERE first_name LIKE ? OR last_name LIKE ? OR phone LIKE ? OR email LIKE ?";
        const term = `%${search}%`;
        params.push(term, term, term, term);
    }

    sql += ' ORDER BY last_name, first_name';
    res.json(db.prepare(sql).all(...params));
});

// Get single customer with vehicles
router.get('/:id', (req, res) => {
    const customer = db.prepare('SELECT * FROM customers WHERE id = ?').get(req.params.id);
    if (!customer) return res.status(404).json({ error: 'Customer not found' });

    const vehicles = db.prepare('SELECT * FROM vehicles WHERE customer_id = ?').all(req.params.id);
    const jobs = db.prepare(`
    SELECT j.*, v.make || ' ' || v.model AS vehicle_name
    FROM service_jobs j JOIN vehicles v ON j.vehicle_id = v.id
    WHERE j.customer_id = ? ORDER BY j.created_at DESC
  `).all(req.params.id);

    res.json({ ...customer, vehicles, jobs });
});

// Create customer
router.post('/', (req, res) => {
    const { first_name, last_name, email, phone, address } = req.body;
    if (!first_name || !last_name || !phone) {
        return res.status(400).json({ error: 'first_name, last_name, and phone are required' });
    }

    const result = db.prepare('INSERT INTO customers (first_name, last_name, email, phone, address) VALUES (?, ?, ?, ?, ?)')
        .run(first_name, last_name, email || null, phone, address || null);

    const customer = db.prepare('SELECT * FROM customers WHERE id = ?').get(result.lastInsertRowid);
    res.status(201).json(customer);
});

// Update customer
router.put('/:id', (req, res) => {
    const existing = db.prepare('SELECT * FROM customers WHERE id = ?').get(req.params.id);
    if (!existing) return res.status(404).json({ error: 'Customer not found' });

    const { first_name, last_name, email, phone, address } = req.body;
    db.prepare('UPDATE customers SET first_name=?, last_name=?, email=?, phone=?, address=? WHERE id=?')
        .run(first_name || existing.first_name, last_name || existing.last_name, email !== undefined ? email : existing.email, phone || existing.phone, address !== undefined ? address : existing.address, req.params.id);

    res.json(db.prepare('SELECT * FROM customers WHERE id = ?').get(req.params.id));
});

// Delete customer
router.delete('/:id', (req, res) => {
    const existing = db.prepare('SELECT * FROM customers WHERE id = ?').get(req.params.id);
    if (!existing) return res.status(404).json({ error: 'Customer not found' });

    db.prepare('DELETE FROM customers WHERE id = ?').run(req.params.id);
    res.json({ message: 'Customer deleted' });
});

module.exports = router;
