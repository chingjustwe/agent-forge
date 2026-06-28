def test_imports():
    from fastapi import FastAPI
    from pydantic import BaseModel
    import httpx
    assert FastAPI
    assert BaseModel
    assert httpx
