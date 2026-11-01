// 检查真实网页：默认启用泛光，开关改变画面，关闭后恢复，模型贴图仍然加载。
async page => {
  const errors = [], checks = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  const assert = (ok, why) => { if (!ok) throw Error(why); };
  await page.setViewportSize({ width:1280, height:800 });
  await page.goto('http://127.0.0.1:8770/walkthrough?model=gltf');
  await page.locator('.walkthrough-loading').waitFor({ state:'hidden', timeout:20000 });
  const toggle = page.getByRole('button', { name:'泛光', exact:true });
  assert(await toggle.count() === 1, '场景尚未提供可见的泛光开关');
  assert(await toggle.getAttribute('aria-pressed') === 'true', '泛光默认未启用');
  const canvas = page.locator('.walkthrough-canvas');
  const settle = async () => {
    await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
    // 截图区域包含工具栏；等待按钮 0.2 秒边框过渡，避免把 UI 过渡误判为模型闪烁。
    await toggle.evaluate(button=>Promise.all(button.getAnimations().map(a=>a.finished.catch(()=>{}))));
  };
  await toggle.evaluate(button=>button.blur()); await page.mouse.move(5,5); await settle();
  const on = await canvas.screenshot({ path:'output/playwright/texture-bloom-on.png' });
  await toggle.click(); await toggle.evaluate(button=>button.blur()); await page.mouse.move(5,5); await settle();
  assert(await toggle.getAttribute('aria-pressed') === 'false', '泛光无法关闭');
  const off = await canvas.screenshot({ path:'output/playwright/texture-bloom-off.png' });
  const metrics = await page.evaluate(async ({on, off}) => {
    const decode = async text => {
      const bytes = Uint8Array.from(atob(text), c => c.charCodeAt(0));
      const bitmap = await createImageBitmap(new Blob([bytes], {type:'image/png'}));
      const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
      const ctx = canvas.getContext('2d'); ctx.drawImage(bitmap,0,0); bitmap.close();
      return ctx.getImageData(0,0,canvas.width,canvas.height).data;
    };
    const a = await decode(on), b = await decode(off);
    let changed=0, gain=0;
    for(let i=0; i<a.length; i+=4) {
      const d=Math.max(Math.abs(a[i]-b[i]),Math.abs(a[i+1]-b[i+1]),Math.abs(a[i+2]-b[i+2]));
      if(d>3)changed++;
      gain+=(a[i]-b[i])+(a[i+1]-b[i+1])+(a[i+2]-b[i+2]);
    }
    return {changedPixels:changed, changedFraction:changed/(a.length/4), meanGain:gain/(a.length/4)/3};
  }, {on:on.toString('base64'),off:off.toString('base64')});
  assert(metrics.changedPixels>100 && metrics.meanGain>0.01, '开启泛光没有产生可见光晕');
  assert(metrics.changedFraction<0.35, '泛光影响范围过大，家具整体过曝');
  checks.push({item:'visible-bloom-on-off',...metrics});
  await toggle.click(); await toggle.evaluate(button=>button.blur()); await page.mouse.move(5,5); await settle();
  const restored=await canvas.screenshot({path:'output/playwright/texture-bloom-restored.png'});
  const stable=await page.evaluate(async ({first,second})=>{
    const decode=async text=>{
      const bytes=Uint8Array.from(atob(text),c=>c.charCodeAt(0));
      const bitmap=await createImageBitmap(new Blob([bytes],{type:'image/png'}));
      const canvas=new OffscreenCanvas(bitmap.width,bitmap.height);
      const ctx=canvas.getContext('2d');ctx.drawImage(bitmap,0,0);bitmap.close();
      return ctx.getImageData(0,0,canvas.width,canvas.height).data;
    };
    const a=await decode(first),b=await decode(second);let maxDifference=0;
    for(let i=0;i<a.length;i++)maxDifference=Math.max(maxDifference,Math.abs(a[i]-b[i]));
    return {maxDifference};
  },{first:on.toString('base64'),second:restored.toString('base64')});
  assert(stable.maxDifference<=1, '重复开关后泛光画面没有稳定恢复：'+JSON.stringify(stable));
  checks.push({item:'stable-toggle',...stable});
  for (const size of [{width:1024,height:768},{width:768,height:1024},{width:360,height:800}]) {
    await page.setViewportSize(size); await settle();
    const box=await canvas.boundingBox(), tool=await toggle.boundingBox();
    assert(box && tool && tool.x>=box.x && tool.x+tool.width<=box.x+box.width, '泛光按钮超出画布');
    const modes=await page.locator('.walkthrough-modes').boundingBox();
    assert(modes && modes.x+modes.width<=tool.x, '泛光按钮与模式切换重叠');
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth), '页面出现横向溢出');
    await page.screenshot({path:`output/playwright/texture-bloom-${size.width}x${size.height}.png`});
    checks.push({item:'responsive',...size});
  }
  const textures=await page.evaluate(()=>performance.getEntriesByType('resource').filter(e=>e.name.endsWith('/textures/Image_0.png')).map(e=>({url:e.name,bytes:e.decodedBodySize})));
  assert(textures.some(t=>t.bytes>0), '模型贴图未加载');
  checks.push({item:'texture-loaded',textures});
  assert(errors.length===0, '浏览器错误：'+errors.join(';'));
  return {passed:true,checks,errors};
}
