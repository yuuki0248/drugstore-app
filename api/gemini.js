export default async function handler(req, res) {
  try {
    const body = req.body;

    const response = await fetch(
      "https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent?key=" + process.env.GEMINI_API_KEY,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
      }
    );

    const data = await response.json();

    res.status(200).json(data);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
}