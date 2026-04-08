const express = require('express');
const bcrypt = require('bcrypt');
const jwt = require('jsonwebtoken');
const db = require('../models/database');
const { authenticate, requireRole } = require('../middleware/auth');

const router = express.Router();

// Seed admin user if no users exist
const userCount = db.prepare('SELECT COUNT(*) as count FROM users').get();
if (userCount.count === 0) {
    const hash = bcrypt.hashSync('admin123', 12);
    db.prepare('INSERT INTO users (username, password_hash, role, full_name) VALUES (?, ?, ?, ?)')
        .run('admin', hash, 'admin', 'Administrator');
    console.log('Default admin user created (admin / admin123) — change this password!');
}

// Login
router.post('/login', (req, res) => {
    const { username, password } = req.body;
    if (!username || !password) {
        return res.status(400).json({ error: 'Username and password are required' });
    }

    const user = db.prepare('SELECT * FROM users WHERE username = ?').get(username);
    if (!user || !bcrypt.compareSync(password, user.password_hash)) {
        return res.status(401).json({ error: 'Invalid credentials' });
    }

    const token = jwt.sign(
        { id: user.id, username: user.username, role: user.role, fullName: user.full_name },
        process.env.JWT_SECRET,
        { expiresIn: '8h' }
    );

    res.json({ token, user: { id: user.id, username: user.username, role: user.role, fullName: user.full_name } });
});

// Get current user
router.get('/me', authenticate, (req, res) => {
    const user = db.prepare('SELECT id, username, role, full_name, created_at FROM users WHERE id = ?').get(req.user.id);
    if (!user) return res.status(404).json({ error: 'User not found' });
    res.json(user);
});

// Create user (admin only)
router.post('/users', authenticate, requireRole('admin'), (req, res) => {
    const { username, password, role, fullName } = req.body;
    if (!username || !password || !fullName) {
        return res.status(400).json({ error: 'username, password, and fullName are required' });
    }

    const validRoles = ['admin', 'technician', 'receptionist'];
    if (role && !validRoles.includes(role)) {
        return res.status(400).json({ error: `role must be one of: ${validRoles.join(', ')}` });
    }

    try {
        const hash = bcrypt.hashSync(password, 12);
        const result = db.prepare('INSERT INTO users (username, password_hash, role, full_name) VALUES (?, ?, ?, ?)')
            .run(username, hash, role || 'technician', fullName);
        res.status(201).json({ id: result.lastInsertRowid, username, role: role || 'technician', fullName });
    } catch (err) {
        if (err.message.includes('UNIQUE')) {
            return res.status(409).json({ error: 'Username already exists' });
        }
        res.status(500).json({ error: 'Failed to create user' });
    }
});

// List users (admin only)
router.get('/users', authenticate, requireRole('admin'), (req, res) => {
    const users = db.prepare('SELECT id, username, role, full_name, created_at FROM users').all();
    res.json(users);
});

module.exports = router;
