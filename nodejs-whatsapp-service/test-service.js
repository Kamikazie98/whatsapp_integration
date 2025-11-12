import express from "express";

const app = express();
app.use(express.json());

app.get("/", (req, res) => {
    res.json({ status: "Test Service Running", timestamp: new Date() });
});

app.get("/qr/:session", (req, res) => {
    // Test without Baileys first
    res.json({ 
        session: req.params.session,
        message: "QR test endpoint working",
        timestamp: new Date()
    });
});

const PORT = 3001;
app.listen(PORT, () => {
    console.log(`✅ Test service running on port ${PORT}`);
    console.log(`🔗 Test URL: http://localhost:${PORT}/`);
});
