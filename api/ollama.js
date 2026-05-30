// ローカル Ollama サーバーへのプロキシ
// Ollama が http://localhost:11434 で起動している必要があります
// レスポンスは Claude API 互換形式で返します

const OLLAMA_URL = "https://spoilage-trial-activator.ngrok-free.dev/api/chat";
const MODEL = "llama3.2";

export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  if (req.method === "OPTIONS") return res.status(200).end();
  if (req.method !== "POST") return res.status(405).end();

  const { messages, system } = req.body;

  // Claude 形式 → Ollama 形式に変換
  // system プロンプトは messages 配列の先頭に role:"system" として追加
  const ollamaMessages = [];
  if (system) {
    ollamaMessages.push({ role: "system", content: system });
  }
  for (const m of messages || []) {
    ollamaMessages.push({ role: m.role, content: m.content });
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 600_000);

  try {
    const response = await fetch(OLLAMA_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "ngrok-skip-browser-warning": "true",
      },
      body: JSON.stringify({
        model: MODEL,
        messages: ollamaMessages,
        stream: false,
      }),
      signal: controller.signal,
    });

    if (!response.ok) {
      const text = await response.text();
      return res.status(502).json({ error: "Ollama エラー", detail: text });
    }

    const data = await response.json();
    const text = data.message?.content || "";

    // Claude API 互換形式で返す
    return res.status(200).json({
      content: [{ type: "text", text }],
    });
  } catch (e) {
    const msg = e.name === "AbortError" ? "タイムアウト（10分）" : e.message;
    return res.status(500).json({ error: "Ollama 呼び出し失敗", detail: msg });
  } finally {
    clearTimeout(timeoutId);
  }
}
