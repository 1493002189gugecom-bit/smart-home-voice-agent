// All APIs are loopback fixtures. This script cannot reach a paid model or device.
async page => {
  const checks = [], posts = [], errors = [];
  const assert = (value, text) => { if (!value) throw Error(text); checks.push(text); };
  page.on('pageerror', error => errors.push(error.message));
  const at = new Date().toISOString();
  const events = [
    { id: 1, timestamp: at, type: 'voice_stage', payload: { turn_id:'one', stage:'intent', status:'completed', duration_ms:844 } },
    { id: 2, timestamp: at, type: 'voice_stage', payload: { turn_id:'one', stage:'playback', status:'running' } },
  ];
  let report = { status:'idle', results:[], summary:{} };
  await page.route('**/api/**', async route => {
    const path = route.request().url().replace(/^https?:\/\/[^/]+/, '').split('?')[0];
    if (route.request().method() === 'POST') posts.push({ path, body: route.request().postDataJSON() });
    const reply = json => route.fulfill({ json });
    if (path === '/api/status') return reply({ ok:true, state:'up', services:{ voice:{ state:'up' }, home:{ state:'up' }, vision:{ state:'up' } } });
    if (path === '/api/voice/history') return reply({events});
    if (path === '/api/voice/events') return route.fulfill({contentType:'text/event-stream',body:': fixture\n\n'});
    if (path === '/api/voice/devices') return reply({inputs:[],outputs:[]});
    if (path === '/api/home/snapshot') return reply({persons:[],rooms:[],devices:[]});
    if (path === '/api/home/persons') return reply({persons:[]});
    if (path === '/api/vision/registrations') return reply({person_ids:[]});
    if (path === '/api/voice/intent/evaluation') return reply(report);
    if (path === '/api/voice/intent/evaluation/start') {
      const body = route.request().postDataJSON();
      report = { status:'completed', total:body.text ? 1 : 24,
        results:[{case_id:'fixture',text:body.text || '昨天热死了',expected:body.text ? null : 'ignore',observed:{kind:'ignore'},passed:body.text ? null : true,duration_ms:844}],
        summary:{completed:body.text ? 1 : 24,scored:body.text ? 0 : 24,passed:24,false_triggers:0,unavailable:0,p50_ms:844,p95_ms:1015} };
      return route.fulfill({status:202,json:{ok:true}});
    }
    return reply({ok:true});
  });
  await page.setViewportSize({width:1440,height:1100});
  await page.goto('http://127.0.0.1:18770');
  await page.getByRole('button',{name:'语音 对话与设备执行'}).click();
  await page.getByText('播放回复…',{exact:true}).waitFor();
  assert(await page.getByText('0.84秒',{exact:true}).isVisible(), 'Completed stage timing is visible beside current playback state');
  await page.getByRole('button',{name:'停止回复',exact:true}).click();
  await page.getByText('已请求停止回复；已确认的设备操作仍然有效。',{exact:true}).waitFor();
  assert(posts[0].path.endsWith('/conversation/stop') && Object.keys(posts[0].body).length === 0,'Stop uses its dedicated endpoint with no device parameters');
  await page.getByText('检查意图识别效果',{exact:true}).click();
  assert(await page.getByText(/可能产生模型费用/).isVisible(), 'Evaluation explains API cost and no device execution before starting');
  await page.getByRole('button',{name:'运行24个关键场景',exact:true}).click();
  await page.getByText(/符合预期 24\/24/).waitFor();
  assert(posts[1].body.limit === 24,'Built-in evaluation is bounded to 24 cases');
  await page.getByRole('textbox',{name:'单句试判'}).fill('还嫌不够热吗？');
  await page.getByRole('button',{name:'只判断',exact:true}).click();
  await page.locator('.voice-evaluation-results').getByText('还嫌不够热吗？',{exact:true}).waitFor();
  assert(posts[2].path.endsWith('/intent/evaluation/start') && posts[2].body.text === '还嫌不够热吗？','Custom preview reaches only the shadow classifier');
  assert(!posts.some(row => row.path.includes('/tool/') || row.path.endsWith('/conversation/text')), 'Evaluation and stopping never submit a device or conversation command');
  await page.screenshot({path:'E:/smart-home/output/playwright/voice-hardening/desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('textbox',{name:'单句试判'}).scrollIntoViewIfNeeded();
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+1),'Evaluation and timing controls fit mobile width');
  await page.screenshot({path:'E:/smart-home/output/playwright/voice-hardening/mobile.png',fullPage:true});
  assert(errors.length === 0,'No browser runtime errors');
  return {checks,posts:posts.length};
}
