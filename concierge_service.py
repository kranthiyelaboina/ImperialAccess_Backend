"""
GenAI Voice Concierge Service
Provides personalized audio greetings using Gemini LLM + Deepgram TTS
Designed for zero-latency real-time deployment in lounge access control
"""

import os
import threading
import queue
import time
import logging
from typing import Optional, Dict, Any
from datetime import datetime
import asyncio
from concurrent.futures import ThreadPoolExecutor

import google.generativeai as genai
from deepgram import DeepgramClient
from deepgram.speak.v1.types import SpeakV1Text
import numpy as np

logger = logging.getLogger(__name__)

# API Keys (from environment)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable is required. Add it to .env file.")

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
if not DEEPGRAM_API_KEY:
    raise ValueError("DEEPGRAM_API_KEY environment variable is required. Add it to .env file.")

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)


class VoiceConciergeService:
    """
    Generates personalized audio greetings for recognized guests.
    
    Features:
    - Async greeting generation (non-blocking)
    - Streaming audio playback
    - Guest context integration (name, flight, tokens)
    - Background thread for audio synthesis
    - Graceful error handling
    - Memory efficient (no large file buffering)
    """

    def __init__(self, max_workers: int = 2):
        self.max_workers = max_workers
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.audio_queue = queue.Queue(maxsize=10)
        self.is_running = False
        self.playback_thread = None
        self._lock = threading.Lock()

        # Initialize Deepgram client
        self.deepgram_client = DeepgramClient(api_key=DEEPGRAM_API_KEY)

        # Model configuration for low latency
        self.gemini_model = "gemini-1.5-flash"  # Faster than Pro
        self.tts_model = "aura-2-thalia-en"  # Natural voice
        self.sample_rate = 16000  # CD quality
        self.encoding = "linear16"

        # Start background playback thread
        self._start_playback_thread()

    def _start_playback_thread(self):
        """Start background thread for audio playback (non-real-time)."""
        if not self.is_running:
            self.is_running = True
            self.playback_thread = threading.Thread(
                target=self._playback_loop, daemon=True
            )
            self.playback_thread.start()
            logger.info("Voice Concierge playback thread started")

    def _playback_loop(self):
        """Background loop: polls queue and handles audio playback."""
        while self.is_running:
            try:
                # Get audio task from queue (non-blocking with timeout)
                task = self.audio_queue.get(timeout=1.0)
                if task is None:  # Shutdown signal
                    break

                guest_id, audio_file_path = task
                self._play_audio_file(audio_file_path)
                logger.info(f"Played greeting for guest {guest_id}")

            except queue.Empty:
                # No audio to play, continue waiting
                continue
            except Exception as e:
                logger.error(f"Playback error: {e}")

    def _play_audio_file(self, file_path: str):
        """
        Play audio file (stub for integration with audio device).
        Production: Replace with actual audio output library.
        """
        try:
            # Check file exists
            if not os.path.exists(file_path):
                logger.warning(f"Audio file not found: {file_path}")
                return

            # Placeholder: In production, use pyaudio, pygame, or system command
            # For now, just verify file was created
            file_size = os.path.getsize(file_path)
            logger.debug(f"Audio file: {file_path} ({file_size} bytes)")

            # Example: Play with Windows command (can be swapped for cross-platform)
            # os.system(f'powershell -c (New-Object System.media.SoundPlayer "{file_path}").PlaySync()')

        except Exception as e:
            logger.error(f"Failed to play audio: {e}")

    def generate_greeting_async(
        self,
        guest_id: str,
        guest_name: str,
        flight_gate: Optional[str] = None,
        flight_time: Optional[str] = None,
        dining_tokens: Optional[Dict[str, int]] = None,
    ) -> bool:
        """
        Generate and queue a personalized greeting (non-blocking).

        Args:
            guest_id: MongoDB guest ID
            guest_name: Full name of guest
            flight_gate: Departure gate (e.g., "4")
            flight_time: Time until departure (e.g., "2 hours 15 minutes")
            dining_tokens: Dict of token counts {"breakfast": 1, "lunch": 0, "dinner": 1}

        Returns:
            True if greeting queued successfully, False otherwise
        """
        try:
            # Submit to thread pool for async processing
            self.executor.submit(
                self._generate_and_save_greeting,
                guest_id,
                guest_name,
                flight_gate,
                flight_time,
                dining_tokens,
            )
            return True

        except Exception as e:
            logger.error(f"Failed to queue greeting for {guest_name}: {e}")
            return False

    def _generate_and_save_greeting(
        self,
        guest_id: str,
        guest_name: str,
        flight_gate: Optional[str] = None,
        flight_time: Optional[str] = None,
        dining_tokens: Optional[Dict[str, int]] = None,
    ):
        """
        Generate greeting text via Gemini, convert to audio via Deepgram,
        and queue for playback. Runs in background thread.
        """
        start_time = time.time()

        try:
            # Step 1: Generate greeting text (via Gemini)
            greeting_text = self._generate_greeting_text(
                guest_name, flight_gate, flight_time, dining_tokens
            )

            if not greeting_text:
                logger.warning(f"No greeting generated for {guest_name}")
                return

            logger.debug(f"Greeting: {greeting_text}")

            # Step 2: Convert text to speech (via Deepgram)
            audio_file = self._text_to_speech(guest_id, greeting_text)

            if not audio_file:
                logger.warning(f"TTS failed for {guest_name}")
                return

            # Step 3: Queue for playback
            self.audio_queue.put((guest_id, audio_file), block=False)

            elapsed = time.time() - start_time
            logger.info(
                f"Greeting generated for {guest_name} in {elapsed:.2f}s"
            )

        except queue.Full:
            logger.warning("Audio queue full, greeting skipped")
        except Exception as e:
            logger.error(f"Greeting generation failed: {e}", exc_info=True)

    def _generate_greeting_text(
        self,
        guest_name: str,
        flight_gate: Optional[str] = None,
        flight_time: Optional[str] = None,
        dining_tokens: Optional[Dict[str, int]] = None,
    ) -> Optional[str]:
        """
        Use Gemini to generate personalized greeting text.
        Optimized for speed and natural speech.
        """
        try:
            # Build context string
            context_parts = [f"Welcome {guest_name}"]

            if flight_gate and flight_time:
                context_parts.append(
                    f"Your flight departs from Gate {flight_gate} in {flight_time}"
                )

            if dining_tokens:
                token_list = []
                for token_type, count in dining_tokens.items():
                    if count > 0:
                        token_list.append(f"{count} {token_type} token{'s' if count > 1 else ''}")

                if token_list:
                    context_parts.append(
                        f"You have {', and '.join(token_list)} available"
                    )

            context = ". ".join(context_parts) + "."

            # Prompt engineering: brief, natural, conversational
            prompt = f"""Generate a short, warm, personalized greeting for an airport lounge guest. 
Keep it to 1-2 sentences max. Natural and conversational tone.

Guest context: {context}

Do NOT include phrases like "How are you today?" or ask questions.
ONLY generate the greeting itself, nothing else."""

            # Call Gemini (streaming for speed)
            model = genai.GenerativeModel(self.gemini_model)
            response = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.7,
                    max_output_tokens=100,  # Keep it short
                ),
                stream=False,
            )

            if response.text:
                return response.text.strip()

            return None

        except Exception as e:
            logger.error(f"Greeting generation failed: {e}")
            return None

    def _text_to_speech(self, guest_id: str, text: str) -> Optional[str]:
        """
        Convert text to speech using Deepgram.
        Streams directly to file with no buffering for low latency.
        """
        try:
            # Output file path
            timestamp = int(time.time() * 1000)  # Milliseconds for uniqueness
            output_file = (
                f"audio_greetings/{guest_id}_{timestamp}.wav"
            )

            # Create directory if needed
            os.makedirs("audio_greetings", exist_ok=True)

            # Connect to Deepgram and stream audio
            with self.deepgram_client.speak.v1.connect(
                model=self.tts_model,
                encoding=self.encoding,
                sample_rate=self.sample_rate,
            ) as connection:
                # Event handlers
                def on_binary(data: bytes) -> None:
                    # Stream audio directly to file
                    with open(output_file, "ab") as f:
                        f.write(data)
                        f.flush()

                def on_error(error) -> None:
                    logger.error(f"TTS error: {error}")

                # Attach handlers
                from deepgram.core.events import EventType

                connection.on(EventType.MESSAGE, on_binary)
                connection.on(EventType.ERROR, on_error)

                # Start listening for events
                connection.start_listening()

                # Send text for TTS
                connection.send_text(SpeakV1Text(text=text))
                connection.send_flush()

                # Wait for completion (max 10 seconds)
                time.sleep(10)
                connection.send_close()

            # Verify file was created
            if os.path.exists(output_file) and os.path.getsize(output_file) > 1000:
                return output_file

            logger.warning(f"TTS produced empty file: {output_file}")
            return None

        except Exception as e:
            logger.error(f"TTS conversion failed: {e}", exc_info=True)
            return None

    def shutdown(self):
        """Gracefully shutdown the concierge service."""
        with self._lock:
            self.is_running = False
            self.audio_queue.put(None)  # Shutdown signal
            self.executor.shutdown(wait=True)
            logger.info("Voice Concierge service shut down")


# Global singleton instance
_concierge_instance: Optional[VoiceConciergeService] = None
_concierge_lock = threading.Lock()


def get_concierge_service() -> VoiceConciergeService:
    """Get or create the global concierge service instance."""
    global _concierge_instance

    if _concierge_instance is None:
        with _concierge_lock:
            if _concierge_instance is None:
                _concierge_instance = VoiceConciergeService()

    return _concierge_instance
