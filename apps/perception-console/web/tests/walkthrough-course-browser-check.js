// 老师要求的验收：在正式网页验证 Three.js 装载、显示 GLB，以及基本行走和环视。
// 使用已安装的 Playwright CLI 执行；不修改页面代码，不向真实设备发送控制请求。
async page => {
  const checks = [], errors = [];
  const assert = (ok, message) => { if (!ok) throw Error(message); };
  const consoleError = message => {
    if (message.type() === 'error' || /Couldn.t load texture|Content Security Policy|WebGL.*(error|warning)/i.test(message.text())) errors.push(message.text());
  };
  const pageError = error => errors.push(error.message);
  page.on('console', consoleError); page.on('pageerror', pageError);
  try {
    await page.setViewportSize({width:1280,height:800});
    const modelRequest = page.waitForResponse(response => response.url().endsWith('/assets/models/smart-home-layout-2026-10-12.glb'));
    const response = await page.goto('http://127.0.0.1:8770/walkthrough');
    assert(response.status() === 200, '漫游页面无法访问');
    const modelResponse = await modelRequest, bytes = await modelResponse.body();
    assert(modelResponse.status() === 200 && bytes.subarray(0,4).toString() === 'glTF', '未成功装载真实 GLB');
    checks.push({item:'load-glb',status:modelResponse.status(),bytes:bytes.length});
    await page.waitForFunction(() => document.querySelector('.walkthrough-page') && !document.querySelector('.walkthrough-loading'));
    const canvas = page.locator('.walkthrough-canvas');
    // 画布截图会包含覆盖其上的 DOM 工具栏。比较相机运动时仅临时隐藏这些覆盖层，
    // 避免按钮 hover/边框过渡冒充相机移动；按钮状态仍由下面的真实 DOM 断言检查。
    const capture = (options={}) => canvas.screenshot({...options,style:'.walkthrough-toolbar,.walkthrough-rooms,.walkthrough-pad,.walkthrough-hint{visibility:hidden!important}'});
    const equivalent = async (first,second) => {
      if(first.equals(second))return true;
      return page.evaluate(async ({first,second})=>{
        const decode=async text=>{
          const bytes=Uint8Array.from(atob(text),c=>c.charCodeAt(0));
          const bitmap=await createImageBitmap(new Blob([bytes],{type:'image/png'}));
          const canvas=new OffscreenCanvas(bitmap.width,bitmap.height);
          const ctx=canvas.getContext('2d');ctx.drawImage(bitmap,0,0);bitmap.close();
          return ctx.getImageData(0,0,canvas.width,canvas.height).data;
        };
        const a=await decode(first),b=await decode(second);
        if(a.length!==b.length)return false;
        for(let i=0;i<a.length;i++)if(Math.abs(a[i]-b[i])>1)return false;
        return true;
      },{first:first.toString('base64'),second:second.toString('base64')});
    };
    await canvas.waitFor({state:'visible'}); await page.waitForTimeout(250);
    await capture({path:'output/playwright/course-overview.png'});
    checks.push({item:'display-scene',canvasVisible:true});
    await page.getByRole('button',{name:'进入漫游',exact:true}).click();

    // 比较实际渲染画面，确认每个房间里按 W 会移动，松键后停止，重置后回到入场视角。
    for (const room of ['客厅','卧室','厨房']) {
      const button = page.getByRole('button',{name:room,exact:true}); await button.click();
      assert(await button.getAttribute('aria-pressed') === 'true', '房间切换失败：'+room);
      const start = await capture({path:'output/playwright/course-'+room+'.png'});
      await page.keyboard.down('w'); await page.waitForTimeout(300); await page.keyboard.up('w');
      await page.waitForTimeout(100); const moved = await capture({path:'output/playwright/course-'+room+'-moved.png'});
      assert(!await equivalent(start,moved), '行走没有改变画面：'+room);
      await page.waitForTimeout(180); const stopped = await capture({path:'output/playwright/course-'+room+'-stopped.png'});
      assert(await equivalent(moved,stopped), '松键后未停止：'+room);
      await page.getByRole('button',{name:'重置视角',exact:true}).click();
      assert(await equivalent(start,await capture()), '重置后没有恢复入场视角：'+room);
      checks.push({item:'walk-room',room,moves:true,stopsOnRelease:true,reset:true});
    }
    await page.getByRole('button',{name:'客厅',exact:true}).click();
    const beforeLook = await capture(), box = await canvas.boundingBox();
    const x = box.x+box.width*.6, y = box.y+box.height*.5;
    await page.mouse.move(x,y); await page.mouse.down(); await page.mouse.move(x+100,y+35,{steps:8}); await page.mouse.up();
    assert(!await equivalent(beforeLook,await capture()), '拖动未改变环视方向');
    await capture({path:'output/playwright/course-look.png'});
    checks.push({item:'drag-look',changesView:true});
    await page.getByRole('button',{name:'全屋查看',exact:true}).click();
    assert(await page.getByRole('button',{name:'全屋查看',exact:true}).getAttribute('aria-pressed') === 'true', '无法返回全屋模式');
    checks.push({item:'overview-mode',canReturn:true});
    assert(errors.length === 0, '浏览器错误：'+JSON.stringify(errors));
    return {verifiedAt:new Date().toISOString(),url:page.url(),checks,errors};
  } finally {
    await page.keyboard.up('w');
    page.off('console',consoleError); page.off('pageerror',pageError);
  }
}
