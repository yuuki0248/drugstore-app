// Anthropic Claude API プロキシ
// 環境変数 ANTHROPIC_API_KEY が必要

export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  if (req.method === "OPTIONS") return res.status(200).end();
  if (req.method !== "POST") return res.status(405).end();

  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return res.status(500).json({ error: "ANTHROPIC_API_KEY が設定されていません" });
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 600_000);

  try {
    const response = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": apiKey,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify(req.body),
      signal: controller.signal,
    });

    const data = await response.json();
    return res.status(response.status).json(data);
  } catch (e) {
    const msg = e.name === "AbortError" ? "タイムアウト（10分）" : e.message;
    return res.status(500).json({ error: "Claude API呼び出し失敗", detail: msg });
  } finally {
    clearTimeout(timeoutId);
  }
}
