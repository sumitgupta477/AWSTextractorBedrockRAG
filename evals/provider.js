// Promptfoo custom provider: sends each question to the deployed RAG API
// (POST /query) and returns the answer plus what was retrieved, so the eval
// can score correctness, faithfulness to the retrieved text, and citations.
//
// Requires RAG_API_URL (the `api_gateway_endpoint` Terraform output) and
// RAG_API_KEY (the `api_key` Terraform output), sent as the x-api-key header
// that the API's Lambda authorizer checks.
// Uses the built-in fetch in Node.js 18+; no extra dependencies.

class RagApiProvider {
  constructor(options = {}) {
    this.config = options.config || {};
    this.url = this.config.url || process.env.RAG_API_URL;
    this.apiKey = this.config.apiKey || process.env.RAG_API_KEY;
    this.timeoutMs = this.config.timeoutMs || 30000;
  }

  id() {
    return "rag-api";
  }

  async callApi(question) {
    if (!this.url) {
      return { error: "RAG_API_URL is not set. Use the api_gateway_endpoint Terraform output." };
    }
    if (!this.apiKey) {
      return { error: "RAG_API_KEY is not set. Use: terraform output -raw api_key" };
    }
    try {
      const res = await fetch(this.url, {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-api-key": this.apiKey },
        body: JSON.stringify({ query: question }),
        signal: AbortSignal.timeout(this.timeoutMs),
      });
      const body = await res.json().catch(() => ({}));
      if (res.status === 401 || res.status === 403) {
        return { error: `RAG API rejected the API key (HTTP ${res.status}); check RAG_API_KEY` };
      }
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
