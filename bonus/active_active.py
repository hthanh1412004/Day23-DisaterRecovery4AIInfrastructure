"""Alternate edge for the bonus; leaves the graded edge unchanged."""
import asyncio
import json
from pathlib import Path
import sqlite3

import httpx
from fastapi import FastAPI, Response
from pydantic import BaseModel, Field

app = FastAPI()
lock = asyncio.Lock()
turn = 0
URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}
DB = Path("bonus/data/conflicts.sqlite")


class Document(BaseModel):
    doc_id: str
    body: str
    logical_clock: int = Field(ge=0)
    region: str = Field(pattern="^[ab]$")


def merge(document):
    """Deterministic LWW: (logical_clock, region, body) total order, tombstones extensible."""
    DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB) as con:
        con.execute("CREATE TABLE IF NOT EXISTS docs(doc_id TEXT PRIMARY KEY, body TEXT, clock INTEGER, region TEXT)")
        con.execute("BEGIN IMMEDIATE")
        old = con.execute("SELECT clock,region,body FROM docs WHERE doc_id=?", (document.doc_id,)).fetchone()
        new = (document.logical_clock, document.region, document.body)
        if old is None or new > old:
            con.execute("INSERT OR REPLACE INTO docs VALUES (?,?,?,?)",
                        (document.doc_id, document.body, document.logical_clock, document.region))
        winner = con.execute("SELECT body,clock,region FROM docs WHERE doc_id=?", (document.doc_id,)).fetchone()
    return dict(doc_id=document.doc_id, body=winner[0], logical_clock=winner[1], region=winner[2])


@app.post("/v1/documents")
def ingest(document: Document):
    # Canonical merge ledger serializes conflicting updates. A production design
    # would exchange this same operation across both region-local ledgers.
    return merge(document)


@app.get("/v1/infer")
async def infer(response: Response, q: str = "hoa don"):
    global turn
    async with lock:
        selected = "a" if turn % 2 == 0 else "b"
        turn += 1
    async with httpx.AsyncClient(timeout=2) as client:
        for region in (selected, "b" if selected == "a" else "a"):
            try:
                upstream = await client.get(f"{URL[region]}/v1/infer", params={"q": q})
                if upstream.status_code == 200:
                    return dict(edge_region=region, **upstream.json())
            except httpx.RequestError:
                pass
    response.status_code = 503
    return {"error": "no_ready_region"}
