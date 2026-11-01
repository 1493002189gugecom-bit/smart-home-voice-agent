// Run with Playwright CLI run-code --filename against an isolated static preview.
// Every API request is intercepted. No microphone, paid model or real device.
async page => {
  const events = [], requests = [], errors = [], checks = [];
  let sequence = 0;
  const add = (type, payload) => events.push({ id: ++sequence, timestamp: new Date().toISOString(), type, payload });
  const assert = (value, message) => { if (!value) throw Error(message); checks.push(message); };
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/**', async route => {
    const path = route.request().url().replace(/^https?:\/\/[^/]+/, '').split('?')[0];
    const fulfill = body => route.fulfill({ json: body });
    if (path === '/api/status') return fulfill({ ok: true, state: 'up', services: { voice: { state: 'up', data: { text_chat_enabled: true } }, home: { state: 'up' }, vision: { state: 'up' } } });
    if (path === '/api/voice/events') return route.fulfill({ contentType: 'text/event-stream', body: ': isolated fixture\n\n' });
    if (path === '/api/voice/history') return fulfill({ ok: true, events });
    if (path === '/api/voice/devices') return fulfill({ inputs: [], outputs: [] });
    if (path === '/api/home/snapshot') return fulfill({ ok: true, persons: [], rooms: [], devices: [] });
    if (path === '/api/home/persons') return fulfill({ persons: [] });
    if (path === '/api/vision/registrations') return fulfill({ person_ids: [] });
    if (path === '/api/voice/conversation/text') {
      const body = route.request().postDataJSON();
      requests.push(body);
      add('transcript', { input_mode: 'text', request_id: body.request_id, transcript: body.text });
      add('text_turn', { input_mode: 'text', request_id: body.request_id, turn_status: 'processing' });
      await route.fulfill({ status: 202, json: { ok: true, request_id: body.request_id } });
      await page.waitForTimeout(2400); // controlled provider latency, not a live API
        if (body.text.includes('昨天')) add('text_turn', { input_mode: 'text', request_id: body.request_id, turn_status: 'ignored', message: '本句未触发家居助手。' });
        else if (body.text.includes('故障')) add('text_turn', { input_mode: 'text', request_id: body.request_id, turn_status: 'failed', message: '意图服务暂时不可用，本句未执行。' });
        else {
          add('agent_reply', { input_mode: 'text', request_id: body.request_id, text: '请告诉我是哪个房间？', ok: false });
          if (body.speak) {
            add('playback_state', { input_mode: 'text', request_id: body.request_id, source_event: 'tts_start' });
            add('playback_state', { input_mode: 'text', request_id: body.request_id, source_event: 'tts_end' });
          }
          add('text_turn', { input_mode: 'text', request_id: body.request_id, turn_status: 'completed', message: '' });
        }
      return;
    }
    return fulfill({ ok: true });
  });
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.goto('http://127.0.0.1:18770');
  await page.getByRole('button', { name: '语音 对话与设备执行' }).click();
  const input = page.getByRole('textbox', { name: '打字与小屋对话' });
  await input.waitFor();
  const speak = page.getByRole('checkbox', { name: '播报回复' });
  assert(await speak.isChecked(), 'Typed replies have an enabled speech toggle by default');
  await input.fill('好热啊');
  await input.dispatchEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true });
  assert(requests.length === 0, 'Chinese IME Enter does not send a message');
  const firstAccepted = page.waitForResponse('**/api/voice/conversation/text');
  await input.press('Enter');
  await firstAccepted;
  await page.getByRole('button', { name: '等待回复…' }).waitFor();
  assert(requests.length === 1 && requests[0].text === '好热啊', 'Enter submits exactly one final transcript');
  assert(requests[0].speak === true && await speak.isDisabled(), 'Speech choice is submitted and locked while waiting');
  assert(!('speaker_id' in requests[0]), 'Typed input does not claim a speaker identity');
  await page.getByText('请告诉我是哪个房间？', { exact: true }).waitFor();
  await page.getByRole('button', { name: '发送', exact: true }).waitFor();
  assert(await input.inputValue() === '', 'Accepted text clears the input and returns to ready');
  await speak.uncheck();
  await input.fill('昨天在外面太热了');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await page.locator('.timeline').getByText('本句未触发家居助手。', { exact: true }).waitFor();
  assert(await page.getByRole('button', { name: '发送', exact: true }).isVisible(), 'Ignored speech shows an explanation and finishes waiting');
  await input.fill('模拟故障');
  await input.press('Shift+Enter');
  assert((await input.inputValue()).includes('\n') && requests.length === 2, 'Shift+Enter creates a line break without sending');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await page.locator('.timeline').getByText('意图服务暂时不可用，本句未执行。', { exact: true }).waitFor();
  await input.fill('好热啊');
  assert(await page.getByRole('button', { name: '发送', exact: true }).isEnabled(), 'Failed turn releases the composer');
  assert(requests[1].speak === false && requests[2].speak === false, 'Muted turns explicitly disable speech');
  assert(events.filter(event => event.type === 'playback_state').length === 2, 'Only the spoken fixture produces playback events');
  add('service_error', { stage: 'tts_synthesis', error_code: 'tts_service_rejected', message: '语音合成服务拒绝请求（403），回复文字仍可查看。' });
  await page.getByText('语音合成服务拒绝请求（403），回复文字仍可查看。', { exact: true }).waitFor();
  assert(await page.locator('.event.service_error strong').textContent() === '错误', 'Service failure shows a readable error with its stage explanation');
  await page.screenshot({ path: 'E:/smart-home/output/playwright/voice-text/desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await input.scrollIntoViewIfNeeded();
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'Composer fits the narrow layout without horizontal overflow');
  await page.screenshot({ path: 'E:/smart-home/output/playwright/voice-text/mobile.png', fullPage: true });
  assert(errors.length === 0, 'No browser runtime errors');
  return { checks, requests: requests.length };
}
