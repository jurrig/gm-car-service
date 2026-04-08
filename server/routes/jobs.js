const express = require('express');
const db = require('../models/database');
const { authenticate } = require('../middleware/auth');

const router = express.Router();
router.use(authenticate);

// List all jobs with filters
router.get('/', (req, res) => {
    const { status, customer_id, assigned_to } = req.query;
    let sql = `
    SELECT j.*, c.first_name || ' ' || c.last_name AS customer_name,
           v.make || ' ' || v.model || ' (' || v.year || ')' AS vehicle_name,
           u.full_name AS technician_name
    FROM service_jobs j
    JOIN customers c ON j.customer_id = c.id
    JOIN vehicles v ON j.vehicle_id = v.id
    LEFT JOIN users u ON j.assigned_to = u.id
    WHERE 1=1
  `;
    const params = [];

    if (status) { sql += ' AND j.status = ?'; params.push(status); }
    if (customer_id) { sql += ' AND j.customer_id = ?'; params.push(customer_id); }
    if (assigned_to) { sql += ' AND j.assigned_to = ?'; params.push(assigned_to); }

    sql += ' ORDER BY j.created_at DESC';

    const jobs = db.prepare(sql).all(...params);
    res.json(jobs);
});

// Get single job with notes
router.get('/:id', (req, res) => {
    const job = db.prepare(`
    SELECT j.*, c.first_name || ' ' || c.last_name AS customer_name,
           v.make || ' ' || v.model || ' (' || v.year || ')' AS vehicle_name,
           u.full_name AS technician_name
    FROM service_jobs j
    JOIN customers c ON j.customer_id = c.id
    JOIN vehicles v ON j.vehicle_id = v.id
    LEFT JOIN users u ON j.assigned_to = u.id
    WHERE j.id = ?
  `).get(req.params.id);

    if (!job) return res.status(404).json({ error: 'Job not found' });

    const notes = db.prepare(`
    SELECT n.*, u.full_name AS author
    FROM job_notes n JOIN users u ON n.user_id = u.id
    WHERE n.job_id = ? ORDER BY n.created_at DESC
  `).all(req.params.id);

    res.json({ ...job, notes });
});

// Create job
router.post('/', (req, res) => {
    const { vehicle_id, customer_id, assigned_to, title, description, estimated_cost, mileage_in } = req.body;
    if (!vehicle_id || !customer_id || !title) {
        return res.status(400).json({ error: 'vehicle_id, customer_id, and title are required' });
    }

    const result = db.prepare(`
    INSERT INTO service_jobs (vehicle_id, customer_id, assigned_to, title, description, estimated_cost, mileage_in)
    VALUES (?, ?, ?, ?, ?, ?, ?)
  `).run(vehicle_id, customer_id, assigned_to || null, title, description || null, estimated_cost || null, mileage_in || null);

    const job = db.prepare('SELECT * FROM service_jobs WHERE id = ?').get(result.lastInsertRowid);
    res.status(201).json(job);
});

// Update job
router.put('/:id', (req, res) => {
    const { status, assigned_to, title, description, estimated_cost, final_cost } = req.body;
    const existing = db.prepare('SELECT * FROM service_jobs WHERE id = ?').get(req.params.id);
    if (!existing) return res.status(404).json({ error: 'Job not found' });

    const updates = {
        status: status || existing.status,
        assigned_to: assigned_to !== undefined ? assigned_to : existing.assigned_to,
        title: title || existing.title,
        description: description !== undefined ? description : existing.description,
        estimated_cost: estimated_cost !== undefined ? estimated_cost : existing.estimated_cost,
        final_cost: final_cost !== undefined ? final_cost : existing.final_cost,
        completed_at: status === 'completed' ? new Date().toISOString() : existing.completed_at,
    };

    db.prepare(`
    UPDATE service_jobs SET status=?, assigned_to=?, title=?, description=?, estimated_cost=?, final_cost=?, completed_at=?, updated_at=datetime('now')
    WHERE id = ?
  `).run(updates.status, updates.assigned_to, updates.title, updates.description, updates.estimated_cost, updates.final_cost, updates.completed_at, req.params.id);

    const job = db.prepare('SELECT * FROM service_jobs WHERE id = ?').get(req.params.id);
    res.json(job);
});

// Add note to job
router.post('/:id/notes', (req, res) => {
    const { note } = req.body;
    if (!note) return res.status(400).json({ error: 'note is required' });

    const job = db.prepare('SELECT id FROM service_jobs WHERE id = ?').get(req.params.id);
    if (!job) return res.status(404).json({ error: 'Job not found' });

    const result = db.prepare('INSERT INTO job_notes (job_id, user_id, note) VALUES (?, ?, ?)')
        .run(req.params.id, req.user.id, note);

    const created = db.prepare('SELECT n.*, u.full_name AS author FROM job_notes n JOIN users u ON n.user_id = u.id WHERE n.id = ?')
        .get(result.lastInsertRowid);
    res.status(201).json(created);
});

// Dashboard stats
router.get('/stats/summary', (req, res) => {
    const stats = {
        pending: db.prepare("SELECT COUNT(*) as count FROM service_jobs WHERE status = 'pending'").get().count,
        in_progress: db.prepare("SELECT COUNT(*) as count FROM service_jobs WHERE status = 'in_progress'").get().count,
        waiting_parts: db.prepare("SELECT COUNT(*) as count FROM service_jobs WHERE status = 'waiting_parts'").get().count,
        completed_today: db.prepare("SELECT COUNT(*) as count FROM service_jobs WHERE status = 'completed' AND date(completed_at) = date('now')").get().count,
        total_active: db.prepare("SELECT COUNT(*) as count FROM service_jobs WHERE status NOT IN ('completed', 'cancelled')").get().count,
    };
    res.json(stats);
});

module.exports = router;
