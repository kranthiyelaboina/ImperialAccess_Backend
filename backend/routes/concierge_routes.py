"""
concierge_routes.py — GenAI Voice Concierge endpoints (Groq LLM + Deepgram TTS)

POST /api/concierge/stream      — SSE streaming chat/greeting via Groq
POST /api/concierge/tts         — Deepgram TTS (male voice) → returns WAV audio
POST /api/concierge/greeting    — Non-streaming greeting (fallback)
POST /api/concierge/chat        — Non-streaming chat (fallback)
GET  /api/concierge/profile     — Guest concierge profile context
"""

import os
import json
import time
import logging
from typing import Optional

from flask import Blueprint, request, jsonify, Response, stream_with_context
from bson import ObjectId

from auth import login_required
from db import guest_profiles_col, dining_tokens_col, attendance_logs_col

logger = logging.getLogger(__name__)

concierge_bp = Blueprint("concierge", __name__)

# ── Groq API (OpenAI-compatible) ──
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY environment variable is required. Add it to .env file.")
GROQ_MODEL = "llama-3.1-8b-instant"

# ── Deepgram TTS ──
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
if not DEEPGRAM_API_KEY:
    raise ValueError("DEEPGRAM_API_KEY environment variable is required. Add it to .env file.")
# Male voice model — aura-2-zeus-en is a powerful, deep male voice
DEEPGRAM_TTS_MODEL = "aura-2-zeus-en"

from openai import OpenAI


def _get_groq():
    """Create a fresh OpenAI client pointing at Groq for each call."""
    return OpenAI(
        api_key=GROQ_API_KEY,
        base_url="https://api.groq.com/openai/v1",
    )


SYSTEM_PROMPT = """You are the AI Concierge for Imperial Access, a premium airport lounge.
Be warm, professional, and helpful. Keep responses concise (2-4 sentences).
You can help with:
- Lounge amenities and services (spa, showers, rest areas, business center)
- Dining recommendations and token usage
- Flight information and gate directions
- Wi-Fi, entertainment, and relaxation
- General travel tips
Sound like a sophisticated concierge at a luxury lounge. Never break character.
Do NOT use markdown formatting, asterisks, or bullet points — speak naturally."""


def _fetch_guest_context(guest_id: str, guest_name: str):
    """Fetch guest profile context from MongoDB."""
    context_parts = []
    flight_info = None
    token_info = None

    try:
        profile = guest_profiles_col().find_one({"_id": ObjectId(guest_id)})
        if profile:
            guest_name = profile.get("full_name", guest_name)
            flight_no = profile.get("flight_number")
            departure = profile.get("departure_time")
            airline = profile.get("airline")
            gate = profile.get("gate")

            if flight_no:
                flight_info = f"Flight {flight_no}"
                if airline:
                    flight_info = f"{airline} {flight_info}"
                if departure:
                    flight_info += f" departing at {departure}"
                if gate:
                    flight_info += f" from Gate {gate}"
                context_parts.append(flight_info)

            tokens = list(dining_tokens_col().find({
                "guest_id": ObjectId(guest_id),
                "redeemed": False,
            }))
            if tokens:
                token_info = f"{len(tokens)} dining token{'s' if len(tokens) > 1 else ''} available"
                context_parts.append(token_info)

    except Exception as e:
        logger.warning(f"Error fetching guest context: {e}")

    return guest_name, context_parts, flight_info, token_info


# =====================================================
# POST /api/concierge/stream  (SSE streaming)
# =====================================================
@concierge_bp.route("/api/concierge/stream", methods=["POST"])
@login_required
def concierge_stream():
    """
    Stream AI concierge response via Server-Sent Events using Groq.
    Supports both greeting generation and chat.
    """
    data = request.get_json() or {}
    message = data.get("message", "")
    guest_name = data.get("guest_name", "Guest")
    guest_id = data.get("guest_id", "")
    is_greeting = data.get("is_greeting", False)

    # Fetch guest context
    if guest_id:
        guest_name, context_parts, flight_info, token_info = _fetch_guest_context(
            guest_id, guest_name
        )
    else:
        context_parts, flight_info, token_info = [], None, None

    # Build the user message
    if is_greeting:
        context = f"Guest name: {guest_name}."
        if context_parts:
            context += " " + ". ".join(context_parts) + "."
        user_msg = (
            f"Generate a short, warm, personalized greeting for this premium airport "
            f"lounge guest. Keep it to 2-3 sentences max. Natural and conversational. "
            f"Do NOT ask questions. ONLY output the greeting.\n\n"
            f"Guest context: {context}"
        )
    else:
        user_msg = message

    system_msg = SYSTEM_PROMPT
    if is_greeting:
        system_msg += f"\nYou are greeting {guest_name} who just arrived at the lounge."

    def generate():
        try:
            client = _get_groq()
            stream = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                stream=True,
                max_tokens=200,
                temperature=0.7,
            )

            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    token = chunk.choices[0].delta.content
                    yield f"data: {json.dumps({'token': token})}\n\n"

            yield "data: [DONE]\n\n"

        except Exception as e:
            logger.error(f"Groq streaming failed [{type(e).__name__}]: {e}", exc_info=True)
            print(f"[CONCIERGE ERROR] {type(e).__name__}: {e}")
            # Send fallback as single chunk
            fallback = (
                f"Welcome to Imperial Access Lounge, {guest_name}. "
                f"We're delighted to have you with us today."
                if is_greeting
                else "I apologize for the brief interruption. Please try again in a moment."
            )
            yield f"data: {json.dumps({'token': fallback})}\n\n"
            yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# =====================================================
# POST /api/concierge/greeting  (non-streaming fallback)
# =====================================================
@concierge_bp.route("/api/concierge/greeting", methods=["POST"])
@login_required
def generate_greeting():
    """Generate personalized greeting text via Groq (non-streaming)."""
    data = request.get_json() or {}
    guest_name = data.get("guest_name", "Guest")
    guest_id = data.get("guest_id")

    if guest_id:
        guest_name, context_parts, flight_info, token_info = _fetch_guest_context(
            guest_id, guest_name
        )
    else:
        context_parts, flight_info, token_info = [], None, None

    context = f"Guest name: {guest_name}."
    if context_parts:
        context += " " + ". ".join(context_parts) + "."

    prompt = (
        f"Generate a short, warm, personalized greeting for a premium airport lounge guest. "
        f"Keep it to 2-3 sentences max. Natural, professional, conversational tone. "
        f"Do NOT ask questions. Do NOT use markdown. ONLY output the greeting.\n\n"
        f"Guest context: {context}"
    )

    try:
        client = _get_groq()
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=150,
            temperature=0.7,
            stream=False,
        )

        greeting_text = response.choices[0].message.content.strip()

        return jsonify({
            "success": True,
            "greeting": greeting_text,
            "guest_name": guest_name,
            "flight_info": flight_info,
            "token_info": token_info,
        })

    except Exception as e:
        logger.error(f"Groq greeting failed: {e}")
        fallback = f"Welcome to Imperial Access Lounge, {guest_name}. We're delighted to have you with us today."
        return jsonify({
            "success": True,
            "greeting": fallback,
            "guest_name": guest_name,
            "fallback": True,
        })


# =====================================================
# POST /api/concierge/chat  (non-streaming fallback)
# =====================================================
@concierge_bp.route("/api/concierge/chat", methods=["POST"])
@login_required
def concierge_chat():
    """AI Concierge chat via Groq (non-streaming fallback)."""
    data = request.get_json() or {}
    message = data.get("message", "")
    guest_name = data.get("guest_name", "Guest")

    if not message:
        return jsonify({"success": False, "error": "No message provided"}), 400

    system_msg = SYSTEM_PROMPT + f"\nYou are speaking with {guest_name}."

    try:
        client = _get_groq()
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": message},
            ],
            max_tokens=200,
            temperature=0.7,
            stream=False,
        )

        reply = response.choices[0].message.content.strip()
        return jsonify({"success": True, "reply": reply})

    except Exception as e:
        logger.error(f"Concierge chat failed: {e}")
        return jsonify({
            "success": True,
            "reply": "I apologize for the brief interruption. Please try again in a moment.",
            "fallback": True,
        })


# =====================================================
# GET /api/concierge/profile
# =====================================================
@concierge_bp.route("/api/concierge/profile", methods=["GET"])
@login_required
def concierge_profile():
    """Get guest context for the concierge interface."""
    from flask import g
    guest_id = request.args.get("guest_id") or getattr(g, "guest_id", None)

    if not guest_id:
        return jsonify({"success": False, "error": "guest_id required"}), 400

    try:
        profile = guest_profiles_col().find_one({"_id": ObjectId(guest_id)})
        if not profile:
            return jsonify({"success": False, "error": "Guest not found"}), 404

        tokens = list(dining_tokens_col().find({
            "guest_id": ObjectId(guest_id),
            "redeemed": False,
        }))

        return jsonify({
            "success": True,
            "profile": {
                "full_name": profile.get("full_name", "Guest"),
                "membership_type": profile.get("membership_type", "standard"),
                "flight_number": profile.get("flight_number"),
                "airline": profile.get("airline"),
                "departure_time": profile.get("departure_time"),
                "gate": profile.get("gate"),
                "face_registered": profile.get("face_registered", False),
                "dining_tokens_remaining": len(tokens),
            },
        })

    except Exception as e:
        logger.error(f"Concierge profile fetch failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# =====================================================
# POST /api/concierge/tts  (Deepgram TTS → WAV audio)
# =====================================================
@concierge_bp.route("/api/concierge/tts", methods=["POST"])
@login_required
def text_to_speech():
    """
    Convert text to speech via Deepgram REST API.
    Uses male voice (aura-2-orpheus-en).
    Returns audio/wav binary for browser playback.
    """
    data = request.get_json() or {}
    text = data.get("text", "")

    if not text:
        return jsonify({"success": False, "error": "No text provided"}), 400

    try:
        import requests as req

        url = "https://api.deepgram.com/v1/speak"
        headers = {
            "Authorization": f"Token {DEEPGRAM_API_KEY}",
            "Content-Type": "application/json",
        }
        params = {
            "model": DEEPGRAM_TTS_MODEL,
            "encoding": "linear16",
            "sample_rate": "24000",
        }

        resp = req.post(
            url,
            json={"text": text},
            headers=headers,
            params=params,
            stream=True,
            timeout=15,
        )

        if resp.status_code != 200:
            logger.error(f"Deepgram TTS returned {resp.status_code}: {resp.text[:300]}")
            return jsonify({
                "success": False,
                "error": f"TTS service returned {resp.status_code}",
            }), 502

        # Stream audio chunks directly to response
        audio_chunks = []
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                audio_chunks.append(chunk)

        audio_data = b"".join(audio_chunks)

        if len(audio_data) < 100:
            return jsonify({"success": False, "error": "TTS produced empty audio"}), 500

        return Response(
            audio_data,
            mimetype="audio/wav",
            headers={
                "Content-Type": "audio/wav",
                "Content-Length": str(len(audio_data)),
                "Cache-Control": "no-cache",
            },
        )

    except Exception as e:
        logger.error(f"TTS conversion failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": f"TTS failed: {str(e)}"}), 500
