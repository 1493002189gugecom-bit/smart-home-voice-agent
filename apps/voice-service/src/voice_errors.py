"""Fixed public error messages; provider exceptions may contain private URLs."""


def failure_details(stage, error):
    stage = getattr(error, "voice_stage", stage)
    from voice_runtime import VoiceCancelled
    if isinstance(error, VoiceCancelled):
        return {"stage": stage, "error_code": "speech_cancelled", "message": "已停止回复，已确认的设备操作仍然有效。", "error_type": "VoiceCancelled"}
    if isinstance(error, TimeoutError):
        return {"stage": stage, "error_code": "tts_timeout" if stage == "tts_synthesis" else "voice_timeout",
                "message": "语音请求超时，请稍后重试；已有文字和设备确认结果仍可查看。", "error_type": "TimeoutError"}
    code, message = {
        "asr": ("asr_failed", "语音转写失败，请重新说一句。"),
        "speaker": ("speaker_failed", "说话人识别失败，请重试或检查声纹服务。"),
        "intent": ("intent_failed", "意图识别失败，本句未执行，请稍后重试。"),
        "agent": ("agent_failed", "对话处理失败，请查看设备确认结果后重试。"),
        "tts_synthesis": ("tts_synthesis_failed", "语音合成失败，回复文字仍可查看，请检查语音服务或网络。"),
        "playback": ("audio_playback_failed", "音频播放失败，请检查扬声器或耳机设备。"),
    }.get(stage, ("voice_processing_failed", "语音处理失败，请重试或检查语音服务。"))
    cause, seen = error, set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if stage == "tts_synthesis" and getattr(cause, "status", None) == 403:
            code, message = "tts_service_rejected", "语音合成服务拒绝请求（403），请检查访问权限、依赖版本及网络。回复文字仍可查看。"
            break
        cause = cause.__cause__
    return {"stage": stage, "error_code": code, "message": message, "error_type": type(error).__name__}
