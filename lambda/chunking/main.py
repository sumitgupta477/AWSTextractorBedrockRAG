"""Chunking Lambda, invoked by the Step Functions ingestion pipeline.

Reads the full result of an async Textract text-detection job (following
pagination), rebuilds the document text page by page, splits it into
overlapping chunks, and writes each chunk to S3 alongside a Bedrock
Knowledge Base metadata file so answers can cite the source document and page.
"""

import json
import os
import re

import boto3

s3 = boto3.client("s3")
textract = boto3.client("textract")

OUTPUT_BUCKET = os.environ["OUTPUT_BUCKET"]
OUTPUT_PREFIX = os.environ.get("OUTPUT_PREFIX", "processed/")
CHUNK_SIZE_CHARS = int(os.environ.get("CHUNK_SIZE_CHARS", "2000"))
CHUNK_OVERLAP_CHARS = int(os.environ.get("CHUNK_OVERLAP_CHARS", "200"))


def lambda_handler(event, context):
    job_id = event["job_id"]
    source_bucket = event["source_bucket"]
    source_key = event["source_key"]

    pages = get_text_by_page(job_id)
    chunks = chunk_pages(pages, CHUNK_SIZE_CHARS, CHUNK_OVERLAP_CHARS)

    doc_prefix = f"{OUTPUT_PREFIX}{safe_name(source_key)}/"
    delete_existing_chunks(doc_prefix)  # re-uploads replace old chunks instead of duplicating them

    for i, chunk in enumerate(chunks):
        chunk_key = f"{doc_prefix}chunk_{i:04d}.txt"
        s3.put_object(
            Bucket=OUTPUT_BUCKET,
            Key=chunk_key,
            Body=chunk["text"].encode("utf-8"),
            ContentType="text/plain; charset=utf-8",
        )
        # Bedrock Knowledge Bases picks up <file>.metadata.json automatically.
        metadata = {
            "metadataAttributes": {
                "source_document": f"s3://{source_bucket}/{source_key}",
                "page_start": chunk["page_start"],
                "page_end": chunk["page_end"],
                "chunk_index": i,
            }
        }
        s3.put_object(
            Bucket=OUTPUT_BUCKET,
            Key=f"{chunk_key}.metadata.json",
            Body=json.dumps(metadata).encode("utf-8"),
            ContentType="application/json",
        )

    print(json.dumps({
        "source": f"s3://{source_bucket}/{source_key}",
        "pages": len(pages),
        "chunks_created": len(chunks),
    }))

    return {
        "status": "SUCCESS",
        "chunks_created": len(chunks),
        "output_prefix": f"s3://{OUTPUT_BUCKET}/{doc_prefix}",
        "source_document": source_key,
    }


def get_text_by_page(job_id):
    """Return {page_number: text} from a completed Textract job, following NextToken."""
    pages = {}
    kwargs = {"JobId": job_id, "MaxResults": 1000}
    while True:
        response = textract.get_document_text_detection(**kwargs)
        for block in response.get("Blocks", []):
            if block.get("BlockType") == "LINE" and block.get("Text"):
                page = block.get("Page", 1)
                pages.setdefault(page, []).append(block["Text"])
        next_token = response.get("NextToken")
        if not next_token:
            break
        kwargs["NextToken"] = next_token
    return [(page, "\n".join(lines)) for page, lines in sorted(pages.items())]


def chunk_pages(pages, size, overlap):
    """Split page text into ~size-character chunks with overlap, breaking on whitespace.

    Each chunk records the first and last page it covers.
    """
    if overlap >= size:
        raise ValueError("CHUNK_OVERLAP_CHARS must be smaller than CHUNK_SIZE_CHARS")

    # Flatten into one string while remembering where each page starts.
    text_parts, page_starts, offset = [], [], 0
    for page, text in pages:
        page_starts.append((offset, page))
        text_parts.append(text)
        offset += len(text) + 2  # account for the "\n\n" separator
    full_text = "\n\n".join(text_parts)

    def page_at(position):
        current = page_starts[0][1] if page_starts else 1
        for start, page in page_starts:
            if start > position:
                break
            current = page
        return current

    chunks, start = [], 0
    while start < len(full_text):
        end = min(start + size, len(full_text))
        if end < len(full_text):
            # Prefer to end on a paragraph, sentence, or word boundary.
            window = full_text[start:end]
            for pattern in ("\n\n", ". ", "\n", " "):
                cut = window.rfind(pattern)
                if cut > size // 2:
                    end = start + cut + len(pattern)
                    break
        text = full_text[start:end].strip()
        if text:
            chunks.append({
                "text": text,
                "page_start": page_at(start),
                "page_end": page_at(max(start, end - 1)),
            })
        if end >= len(full_text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def delete_existing_chunks(prefix):
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=OUTPUT_BUCKET, Prefix=prefix):
        keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
        if keys:
            s3.delete_objects(Bucket=OUTPUT_BUCKET, Delete={"Objects": keys, "Quiet": True})


def safe_name(key):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", key).strip("_")
