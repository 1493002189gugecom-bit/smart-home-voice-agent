import assert from 'node:assert/strict';
import test from 'node:test';
import { voiceErrorText } from './voice-error-text.ts';

test('structured service errors display their useful explanation', () => {
  assert.equal(voiceErrorText({ message: '语音合成服务拒绝请求（403）。' }), '语音合成服务拒绝请求（403）。');
  assert.match(voiceErrorText({ stage: 'playback' }), /耳机/);
  assert.match(voiceErrorText({ stage: 'tts_synthesis' }), /合成失败/);
});

test('legacy voice_error records remain readable without exposing raw identifiers', () => {
  assert.match(voiceErrorText({ source_event: 'voice_error', error_type: 'RuntimeError' }), /语音处理失败/);
  assert.match(voiceErrorText({ error_type: 'audio_input_open_failed' }), /麦克风/);
});
