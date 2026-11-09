// Promptfoo custom provider: sends each question to the deployed RAG API
// (POST /query) and returns the answer plus what was retrieved, so the eval
// can score correctness, faithfulness to the retrieved text, and citations.
//
// Requires RAG_API_URL: the `api_gateway_endpoint` Terraform output.
// Uses the built-in fetch in Node.js 18+; no extra dependencies.

class RagApiProvider {
  constructor(options = {}) {
    this.config = options.config || {};
    this.url = this.config.url || process.env.RAG_API_URL;
    this.timeoutMs = this.config.timeoutMs || 30000;
  }

  id() {
    return "rag-api";
  }

  async callApi(question) {
    if (!this.url) {
      return { error: "RAG_API_URL is not set. Use the api_gateway_endpoint Terraform output." };
    }
    try {
      const res = await fetch(this.url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: question }),
        signal: AbortSignal.timeout(this.timeoutMs),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        return { error: `RAG API returned HTTP ${res.status}: ${JSON.stringify(body).slice(0, 200)}` };
      }

      // RetrieveAndGenerate citations: each has retrievedReferences with the
      // chunk text, its S3 location, and the metadata written by the chunking
      // Lambda (source_document, page_start, page_end).
      const refs = (body.citations || []).flatMap((c) => c.retrievedReferences || []);
      const contexts = refs.map((r) => r.content?.text).filter(Boolean);
      const sources = [
        ...new Set(refs.map((r) => r.metadata?.source_document || r.location?.s3Location?.uri).filter(Boolean)),
      ];

      return {
        output: body.answer || "",
        metadata: { contexts, sources, num_citations: refs.length },
      };
    } catch (err) {
      return { error: `RAG API call failed: ${err.message}` };
    }
  }
}

module.exports = RagApiProvider;
