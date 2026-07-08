from fastapi import FastAPI
from pydantic import BaseModel
from FlagEmbedding import BGEM3FlagModel

app = FastAPI()
model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True)

class EmbedRequest(BaseModel):
    texts: list[str]

@app.post("/embed")
def embed(req: EmbedRequest):
    vecs = model.encode(req.texts)["dense_vecs"]
    return {"embeddings": [v.tolist() for v in vecs]}

@app.get("/health")
def health():
    return {"status": "ok"}
