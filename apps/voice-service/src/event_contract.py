"""Which internal events may leave the voice service, and which fields with them.

This lives outside `loop.py` so the whitelist is a testable contract instead of a
local of `main()`. The failure it guards against is silent by nature: an event field
that is not listed here is dropped on the way to the console, so a newly added field
would simply never appear and nothing anywhere would report an error.
"""

from __future__ import annotations

# internal log event name -> the public event type it becomes
PUBLIC_EVENT_TYPES = {
    "ready": "voice_state",
    "wake": "voice_state",
    "idle_timeout": "voice_state",
    "text_session_start": "voice_state",
    "text_session_end": "voice_state",
    "agent_end_conversation": "voice_state",
    "exit_command": "voice_state",
    "stopped": "voice_state",
    "asr": "transcript",
    "voice_enrollment": "voice_enrollment",
    "agent_reply": "agent_reply",
    "tool_result": "tool_result",
    "tts_start": "playback_state",
    "tts_end": "playback_state",
    "tts_interrupted": "playback_state",
    # The user talked over the assistant while it was still thinking or
    # synthesizing, so no reply was played at all. It shares the playback event
    # type because the console's one question is "is the assistant talking now?".
    "agent_interrupted": "playback_state",
    "tts_error": "service_error",
    "agent_error": "service_error",
    "voice_error": "service_error",
    "audio_status": "service_error",
}

# public event type -> exactly the fields that may be published for it
PUBLIC_FIELDS = {
    "voice_state": {"state", "keyword", "reason", "barge_in"},
    "transcript": {
        "state", "transcript", "seconds",
        # Speaker fields are published whenever voiceprints are configured, and
        # `speaker_reason` carries why there is no name (audio too short, nobody
        # enrolled, gallery down). Without it the console cannot tell those apart.
        "speaker_id", "speaker_name", "speaker_confidence", "speaker_state", "speaker_reason",
    },
    "agent_reply": {"state", "text", "ok", "error_code", "end_conversation", "tools", "seconds", "cancelled"},
    # Enrolment progress. `enroll_state` is separate from `state`, which always
    # means the voice loop's own state.
    "voice_enrollment": {
        "state", "person_id", "accepted", "required", "min_seconds",
        "last_reason", "enroll_state", "message",
    },
    "tool_result": {"state", "name", "ok", "error_code", "phrase", "operation_id"},
    "playback_state": {
        "state", "text", "synth_seconds", "keyword", "interrupted", "barge_in_hint",
        # `reason` separates "talked over it" from "said the wake word"; without it
        # the console cannot explain why playback stopped. `captured_seconds` is how
        # much of the new command the interruption already captured.
        "reason", "captured_seconds",
    },
    "service_error": {"state", "error_type"},
}
