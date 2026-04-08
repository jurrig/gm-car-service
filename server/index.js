require('dotenv').config();
const express = require('express');
const cors = require('cors');
const helmet = require('helmet');
const rateLimit = require('express-rate-limit');
const db = require('./models/database');
const authRoutes = require('./routes/auth');
const jobRoutes = require('./routes/jobs');
const customerRoutes = require('./routes/customers');
const vehicleRoutes = require('./routes/vehicles');

const app = express();
const PORT = process.env.PORT || 5000;

// Security middleware
app.use(helmet());
app.use(cors({ origin: 'http://localhost:3000', credentials: true }));
app.use(express.json());

const limiter = rateLimit({
    windowMs: 15 * 60 * 1000,
    max: 200,
    standardHeaders: true,
    legacyHeaders: false,
});
app.use(limiter);

// Routes
app.use('/api/auth', authRoutes);
app.use('/api/jobs', jobRoutes);
app.use('/api/customers', customerRoutes);
app.use('/api/vehicles', vehicleRoutes);

// Health check
app.get('/api/health', (req, res) => {
    res.json({ status: 'ok', timestamp: new Date().toISOString() });
});

app.listen(PORT, () => {
    console.log(`GMcarservice API running on port ${PORT}`);
});
