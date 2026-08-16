/**
 * Read Server-Sent Events from a fetch Response body.
 * Yields { data: string } objects for each SSE event.
 */
export async function* readSSE(response: Response): AsyncGenerator<{ data: string }> {
  if (!response.body) throw new Error("No response body");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith("data: ")) {
          yield { data: trimmed.slice(6) };
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}
