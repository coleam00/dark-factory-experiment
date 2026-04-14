"""Conversation management routes."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.db import repository

router = APIRouter()


class ConversationCreate(BaseModel):
    title: str = "New Conversation"


@router.get("/conversations")
async def list_conversations():
    return await repository.list_conversations()


@router.post("/conversations", status_code=201)
async def create_conversation(body: ConversationCreate | None = None):
    """Create a new empty conversation. Body is optional; defaults to title='New Conversation'."""
    title = body.title if body else "New Conversation"
    return await repository.create_conversation(title)


@router.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    conv = await repository.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = await repository.list_messages(conv_id)
    return {**conv, "messages": messages}


@router.delete("/conversations/{conv_id}", status_code=204)
async def delete_conversation(conv_id: str):
    deleted = await repository.delete_conversation(conv_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")


@router.get("/videos")
async def list_videos():
    return await repository.list_videos()
